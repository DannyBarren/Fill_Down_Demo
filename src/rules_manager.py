"""Keyword-rule management and matching.

Rules give users a fast, deterministic override for the patterns they
already know (e.g. "Cloud Hosting" -> "6100"). They are persisted in SQLite via the
:class:`~utils.storage.Storage` layer and always take precedence over the
fuzzy similarity engine.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from models.schemas import KeywordRule
from src.data_loader import NEW_ACCOUNT_COL, RULE_NOTES_COL
from utils.account_codes import normalize_code
from utils.logging_setup import get_logger
from utils.storage import Storage

logger = get_logger(__name__)

# Collapse anything that isn't a letter/digit to spaces, lower-cased. This makes
# matching robust to punctuation/spacing differences ("Cloud-Host", "CLOUD HOST").
_RULE_NONALNUM = re.compile(r"[^a-z0-9]+")
# Default similarity ratio a window must reach for a "fuzzy" rule to match.
_FUZZY_CUTOFF = 0.84


def _norm_space(text: str) -> str:
    """Lower-case and collapse runs of non-alphanumerics to single spaces."""
    return _RULE_NONALNUM.sub(" ", str(text).lower()).strip()


def _norm_tight(text: str) -> str:
    """Lower-case and strip every non-alphanumeric (so 'Cloud Host' -> 'cloudhost')."""
    return _RULE_NONALNUM.sub("", str(text).lower())


@dataclass
class RuleMatch:
    account_code: str
    rule: KeywordRule


class RulesManager:
    """CRUD + matching for keyword rules, backed by persistent storage."""

    def __init__(self, storage: Storage):
        self.storage = storage

    # ----------------------------------------------------------------- CRUD
    def add_rule(self, keyword: str, account_code: str, **kwargs) -> KeywordRule:
        rule = KeywordRule(
            keyword=keyword,
            account_code=normalize_code(account_code),
            **kwargs,
        )
        saved = self.storage.add_rule(rule)
        logger.info("rule_added", keyword=saved.keyword, code=saved.account_code)
        return saved

    def update_rule(self, rule: KeywordRule) -> None:
        rule.account_code = normalize_code(rule.account_code)
        self.storage.update_rule(rule)
        logger.info("rule_updated", id=rule.id)

    def delete_rule(self, rule_id: int) -> None:
        self.storage.delete_rule(rule_id)
        logger.info("rule_deleted", id=rule_id)

    def delete_rules(self, rule_ids: List[int]) -> int:
        """Bulk-delete rules by id. Returns how many were removed."""
        removed = self.storage.delete_rules(rule_ids)
        logger.info("rules_deleted", count=removed)
        return removed

    def clear_rules(self) -> int:
        """Delete every keyword rule. Returns how many were removed."""
        removed = self.storage.clear_rules()
        logger.info("rules_cleared", count=removed)
        return removed

    def list_rules(self, enabled_only: bool = False) -> List[KeywordRule]:
        return self.storage.list_rules(enabled_only=enabled_only)

    # ------------------------------------------------------------- matching
    def match_row(
        self,
        combined_text: str,
        row: Optional[pd.Series] = None,
        rules: Optional[List[KeywordRule]] = None,
    ) -> Optional[RuleMatch]:
        """Return the first enabled rule that matches this row, else ``None``.

        ``combined_text`` is the pre-built lowercased similarity text. ``row``
        (optional) lets field-scoped rules inspect individual columns.
        """
        rules = rules if rules is not None else self.list_rules(enabled_only=True)
        for rule in rules:
            if not rule.enabled:
                continue
            if self._rule_matches(rule, combined_text, row):
                return RuleMatch(account_code=rule.account_code, rule=rule)
        return None

    @staticmethod
    def _haystacks(
        rule: KeywordRule,
        combined_text: str,
        row: Optional[pd.Series],
    ) -> List[str]:
        """The text fragments a rule should be tested against.

        Field-scoped rules look only at their named columns. Otherwise we search
        **every real text column on the row** (Memo, Description, Payee, Name,
        Split, …) *and* the pre-built combined/sim text. This is the key
        reliability fix: rules no longer miss vendors that live in a column which
        happens not to be one of the configured similarity columns.
        """
        haystacks: List[str] = []
        if rule.fields:
            if row is None:
                return []
            for fname in rule.fields:
                if fname in row and pd.notna(row[fname]):
                    haystacks.append(str(row[fname]))
            return haystacks

        if row is not None:
            for col, val in row.items():
                name = str(col)
                # Skip internal/meta columns, the answer column, and Rule Notes
                # (its text is already folded into combined_text).
                if name.startswith("_") or name == NEW_ACCOUNT_COL \
                        or name == RULE_NOTES_COL:
                    continue
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    continue
                sval = str(val).strip()
                if sval:
                    haystacks.append(sval)
        if combined_text:
            haystacks.append(str(combined_text))
        if not haystacks:
            haystacks.append(str(combined_text or ""))
        return haystacks

    @staticmethod
    def _fuzzy_contains(cand_space: str, key_space: str,
                        cutoff: float = _FUZZY_CUTOFF) -> bool:
        """True if a window of the candidate ~matches the keyword (typo-tolerant)."""
        if not key_space or not cand_space:
            return False
        if key_space in cand_space:
            return True
        ktokens = key_space.split()
        ctokens = cand_space.split()
        if not ktokens or not ctokens:
            return False
        width = len(ktokens)
        last = max(1, len(ctokens) - width + 1)
        for start in range(last):
            window = " ".join(ctokens[start:start + width])
            if difflib.SequenceMatcher(None, key_space, window).ratio() >= cutoff:
                return True
        return False

    @staticmethod
    def _rule_matches(
        rule: KeywordRule,
        combined_text: str,
        row: Optional[pd.Series],
    ) -> bool:
        keyword = (rule.keyword or "").strip()
        if not keyword:
            return False
        haystacks = RulesManager._haystacks(rule, combined_text, row)
        if rule.fields and not haystacks:
            return False

        for hay in haystacks:
            # ---- regex: applied to the raw fragment ------------------------
            if rule.match_type == "regex":
                flags = 0 if rule.case_sensitive else re.IGNORECASE
                try:
                    if re.search(keyword, hay, flags):
                        return True
                except re.error:
                    logger.warning("invalid_regex_rule", keyword=keyword)
                continue

            # ---- case-sensitive: literal comparison (no normalisation) -----
            if rule.case_sensitive:
                if rule.match_type == "exact":
                    if hay.strip() == keyword.strip():
                        return True
                elif keyword in hay:
                    return True
                continue

            # ---- default case-insensitive, punctuation/space tolerant ------
            cand_space = _norm_space(hay)
            key_space = _norm_space(keyword)
            if not key_space:
                continue

            if rule.match_type == "exact":
                if cand_space == key_space:
                    return True
                continue
            if rule.match_type == "fuzzy":
                if RulesManager._fuzzy_contains(cand_space, key_space):
                    return True
                continue

            # "contains" (default): spaced substring, plus a tight (de-spaced)
            # pass so multi-word keywords match concatenated source text
            # (e.g. "Cloud Hosting" matches "CloudHosting").
            if key_space in cand_space:
                return True
            if " " in key_space and _norm_tight(keyword) in _norm_tight(hay):
                return True
        return False

    def seed_default_rules(self) -> None:
        """Add a couple of illustrative rules on first run (idempotent)."""
        if self.list_rules():
            return
        self.add_rule("Cloud Hosting", "6100", notes="Example rule (software/IT).")
        self.add_rule("SaaS Tools", "6100", notes="Software subscriptions.")
        logger.info("default_rules_seeded")
