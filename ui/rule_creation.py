"""Rule creation UX — dialog, selection strip, seed prompts.

``st.data_editor`` renders to a canvas and cannot expose per-cell right-click
text, so this module relies on three *reliable* triggers that always work:

1. **Checkbox selection** → selection strip + toolbar button. Selecting a single
   row exposes a per-column picker (the dependable "create a rule from this
   cell" path).
2. **Target Account inline prompt** — after typing a code, a one-click prompt
   offers to turn it into a reusable rule.
3. **Seeding banner** — coded rows surface candidate rules for bulk review.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import streamlit as st

from src import spreadsheet_helpers as sh
from src.data_loader import RULE_NOTES_COL
from src.rules_manager import RulesManager
from ui import common
from utils.account_codes import describe, normalize_code


def open_rule_panel(prefill: Optional[Dict] = None) -> None:
    """Open the rule-creation dialog with optional prefill."""
    st.session_state["rule_panel_open"] = True
    if prefill:
        st.session_state["rule_prefill"] = prefill
        st.session_state["rulepanel_keyword"] = str(prefill.get("keyword", ""))
        st.session_state["rulepanel_code"] = str(prefill.get("code", ""))
        st.session_state["rulepanel_notes"] = str(prefill.get("notes", ""))


def close_rule_panel() -> None:
    st.session_state["rule_panel_open"] = False


def queue_target_edit_prompts(
    target_edits: List[Dict],
    rules_manager,
) -> None:
    """After a Target Account edit, queue one inline 'create rule?' prompt."""
    if st.session_state.get("rule_prompt"):
        return
    dismissed = set(st.session_state.get("dismissed_rule_prompts", []))
    for edit in target_edits or []:
        kw = str(edit.get("keyword", "")).strip()
        code = str(edit.get("code", "")).strip()
        key = f"{kw.lower()}|{code}"
        if key in dismissed:
            continue
        if sh.rule_prompt_worthy(kw, code, rules_manager):
            st.session_state["rule_prompt"] = {
                "keyword": kw,
                "code": code,
                "row": int(edit.get("row", -1)),
            }
            return


def render_selection_strip(work, loaded, rules_manager, counts: dict,
                           config) -> None:
    """Compact action bar when rows are selected — fast path to rule creation.

    Selecting exactly one row reveals a per-column picker: the reliable,
    canvas-safe equivalent of "create a rule from this cell".
    """
    sel = sh.selected_indices(work)
    if not sel:
        return

    kw_cols = sh.rule_keyword_columns(work, loaded)
    prefill = sh.rule_creation_prefill(work, sel, loaded,
                                       rules_manager=rules_manager)
    keyword = str(prefill.get("keyword", ""))
    code = str(prefill.get("code", ""))

    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([3, 2, 2, 1.2])
        c1.markdown(
            f"**{len(sel)} row(s) selected** — "
            f"keyword: `{keyword or '—'}`"
            + (f" → **{code}**" if code else ""))
        if len(sel) == 1 and kw_cols:
            focus = c2.selectbox(
                "Keyword from column",
                ["Best guess"] + kw_cols,
                key="rule_focus_column",
                label_visibility="collapsed",
                help="Use the best-guess keyword, or take it from one column "
                     "of this row.")
            if focus != "Best guess":
                focused_kw = sh.suggest_keyword_from_cell(
                    work, sel[0], focus, loaded)
                if focused_kw:
                    keyword = focused_kw
                    prefill["keyword"] = keyword
                    prefill["column"] = focus
                    prefill["fields"] = [focus]
        else:
            c2.caption("Tip: select one row to pull the keyword from a column.")

        if keyword:
            cnt, _ = sh.rule_preview(
                work, keyword, "contains", False,
                list(prefill.get("fields") or []), loaded, config)
            c3.caption(f"Would match **{cnt}** row(s) in this file.")

        if c4.button("Create rule", type="primary", width="stretch",
                     key="strip_create_rule"):
            open_rule_panel(prefill)
            st.rerun()


def render_target_account_prompt(work, loaded) -> None:
    """Inline Yes/No prompt after the user types a Target Account code."""
    prompt = st.session_state.get("rule_prompt")
    if not prompt:
        return

    kw = str(prompt.get("keyword", "")).strip()
    code = normalize_code(str(prompt.get("code", "")))
    if not kw or not code:
        st.session_state.pop("rule_prompt", None)
        return

    st.markdown(
        f"<div class='pc-rule-prompt'>Create a reusable rule for "
        f"<strong>{kw}</strong> → <strong>{code}</strong>?</div>",
        unsafe_allow_html=True,
    )
    y, n, _ = st.columns([1, 1, 6])
    if y.button("Yes, create rule", type="primary", key="rule_prompt_yes"):
        row = int(prompt.get("row", -1))
        indices = [row] if 0 <= row < len(work) else sh.selected_indices(work)
        prefill = sh.rule_creation_prefill(work, indices, loaded)
        prefill["keyword"] = kw
        prefill["code"] = code
        st.session_state.pop("rule_prompt", None)
        open_rule_panel(prefill)
        st.rerun()
    if n.button("Not now", key="rule_prompt_no"):
        dismissed = list(st.session_state.get("dismissed_rule_prompts", []))
        dismissed.append(f"{kw.lower()}|{code}")
        st.session_state["dismissed_rule_prompts"] = dismissed[-40:]
        st.session_state.pop("rule_prompt", None)
        st.rerun()


def render_seed_suggestion_banner(work, loaded, rules_manager) -> None:
    """Banner when coded rows yield candidate rules (upload / seeding)."""
    if st.session_state.get("hide_rule_suggestions"):
        return
    try:
        cands = sh.candidate_rules(work, loaded, rules_manager)
    except Exception:  # noqa: BLE001
        return
    if not cands:
        return

    n = len(cands)
    st.markdown(
        f"<div class='pc-seed-banner'>"
        f"<strong>{n} potential rule{'s' if n != 1 else ''}</strong> detected "
        f"from your seeded Target Account values. "
        f"Review and create them once — they apply to every future file."
        f"</div>",
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns([1.4, 1.4, 4])
    if c1.button("Review and create", type="primary", width="stretch",
                 key="banner_review_create"):
        st.session_state["panel"] = "rules"
        st.session_state["export_open"] = False
        st.rerun()
    if c2.button("Dismiss", width="stretch", key="banner_dismiss_rules"):
        st.session_state["hide_rule_suggestions"] = True
        st.rerun()


@st.dialog("Create rule", width="large")
def rule_creation_dialog(work, loaded, config, rules, storage) -> None:
    """Modal rule builder with live preview."""
    prefill = st.session_state.get("rule_prefill", {})
    indices = list(prefill.get("indices") or sh.selected_indices(work))
    kw_cols = sh.rule_keyword_columns(work, loaded)

    st.caption(
        "Turn a vendor pattern into a permanent keyword rule. The keyword is "
        "matched against all transaction text (Name, Payee, Description, Memo); "
        "narrow it to one column below if needed.")

    c1, c2, c3 = st.columns([3, 2, 2])
    keyword = c1.text_input(
        "When you see this word or phrase",
        key="rulepanel_keyword",
        placeholder="e.g. cloud hosting, acme supplies")
    code = c2.text_input(
        "Use this Target Account",
        key="rulepanel_code",
        placeholder="e.g. 6100")
    match_type = c3.selectbox(
        "Match type", ["contains", "exact", "fuzzy", "regex"],
        key="rulepanel_match",
        help="contains · exact · fuzzy (typos) · regex")

    c4, c5, c6 = st.columns([2, 2, 2])
    focus_default = str(prefill.get("column", ""))
    focus_options = ["All columns"] + kw_cols
    focus_idx = (focus_options.index(focus_default)
                 if focus_default in focus_options else 0)
    focus = c4.selectbox(
        "Search in column",
        focus_options,
        index=focus_idx,
        key="rulepanel_focus",
        help="Limit the rule to one column, or search all text columns.")
    notes = c5.text_input(
        "Rule notes (optional)",
        key="rulepanel_notes",
        placeholder=str(prefill.get("notes_placeholder", "")
                      or "Optional hint for the matcher"))
    case_sensitive = c6.checkbox("Case sensitive", value=False,
                                 key="rulepanel_case")

    fields: List[str] = []
    if focus and focus != "All columns":
        fields = [focus]

    keyword = keyword.strip()
    if keyword:
        count, sample = sh.rule_preview(
            work, keyword, match_type, case_sensitive, fields, loaded, config)
        st.markdown(f"**Live preview — {count} row(s) would match**")
        if not sample.empty:
            st.dataframe(sample, width="stretch", hide_index=True, height=160)
        if code.strip():
            norm = normalize_code(code)
            desc = describe(norm)
            st.caption(
                f"Will set Target Account to **{norm}**"
                + (f" ({desc})" if desc else ""))
    else:
        st.caption("Enter a keyword to see the live match preview.")

    b1, b2, b3 = st.columns([2, 2, 1])
    create = b1.button(
        "Create rule and apply", type="primary", width="stretch",
        disabled=not (keyword and code.strip()),
        key="rulepanel_create_btn")
    stamp_notes = b2.checkbox(
        "Stamp rule notes on matched rows",
        value=bool(str(prefill.get("notes", "")).strip()),
        key="rulepanel_applynotes")
    if b3.button("Cancel", width="stretch", key="rulepanel_cancel"):
        close_rule_panel()
        st.rerun()

    if create:
        _save_rule_from_dialog(
            work, loaded, config, rules, storage,
            keyword, code.strip(), match_type, case_sensitive,
            fields, notes.strip(), stamp_notes)


def _save_rule_from_dialog(work, loaded, config, rules, storage,
                           keyword, code, match_type, case_sensitive,
                           fields, notes, stamp_notes) -> None:
    common.push_undo()
    rules.add_rule(
        keyword, code, match_type=match_type,
        case_sensitive=case_sensitive, fields=fields, notes=notes)

    if stamp_notes and notes:
        from models.schemas import KeywordRule
        rule = KeywordRule(
            keyword=keyword, account_code="0", match_type=match_type,
            case_sensitive=case_sensitive, fields=fields)
        for i in range(len(work)):
            sim = work.iloc[i][sh.SIM_TEXT_COL] if sh.SIM_TEXT_COL in work.columns else ""
            if RulesManager._rule_matches(rule, sim, work.iloc[i]):
                work.at[work.index[i], RULE_NOTES_COL] = notes
        from src.data_loader import recompute_sim_text
        recompute_sim_text(work, loaded.text_columns, config)
        sh._persist_changed_notes(work, storage)

    applied = sh.run_rules_only(work, rules, loaded, config, overwrite=False)
    close_rule_panel()
    st.session_state["data_version"] = st.session_state.get("data_version", 0) + 1
    common.set_flash(
        f"Created rule '{keyword}' → {normalize_code(code)} and filled "
        f"{applied} matching row(s). Saved for future files.")
    st.rerun()
