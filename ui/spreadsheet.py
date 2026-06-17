"""The dominant Spreadsheet workspace — the heart of v2.2.

Design notes on editor <-> work_df sync (the part most likely to drift):

* ``work_df`` (in session state) is the single source of truth.
* The editor is rendered for the current *page* of the current *filter*. Its
  return value is committed straight back into ``work_df`` by row index on every
  rerun (auto-save), so manual edits, selections and Rule Notes never get lost.
* Programmatic mutations (runs, bulk actions, undo/redo, applying a rule) bump a
  ``data_version`` counter. The editor's widget ``key`` includes that counter,
  the page and the filter — so after any programmatic change the grid reloads
  cleanly from ``work_df`` instead of replaying stale edit deltas.
"""

from __future__ import annotations

import streamlit as st

from src import spreadsheet_helpers as sh
from src.data_loader import RULE_NOTES_COL
from src.exporter import (
    export_dataframe,
    export_preserving_original,
    export_csv,
)
from src.similarity import semantic_backend_available
from ui import common
from ui import rule_creation as rc
from ui.common import services

FILTER_MODES = ["All", "Review only", "High confidence", "Blanks only",
                "Filled only"]
PAGE_SIZES = [100, 250, 500, 1000, 1500, 2000, 5000]
EDITOR_HEIGHT = 720


def _bump() -> None:
    st.session_state["data_version"] = st.session_state.get("data_version", 0) + 1


def render_spreadsheet() -> None:
    config, storage, rules, mm, logger = services()
    st.session_state.setdefault("data_version", 0)

    work = st.session_state.get("work_df")
    loaded = st.session_state.get("loaded")
    if work is None or loaded is None:
        st.info("No file loaded yet.")
        if st.button("Go to upload", type="primary"):
            st.session_state["view"] = "landing"
            st.rerun()
        return

    counts = sh.summary_counts(work, loaded)

    _render_header(loaded, counts)
    rc.render_seed_suggestion_banner(work, loaded, rules)

    actions = _render_toolbar(work, loaded, counts, config)

    if counts["selected"]:
        rc.render_selection_strip(work, loaded, rules, counts, config)

    # ---- Filter bar (global search + quick views) -> mask over ALL records.
    mask = _render_filter_bar(work, loaded, config)
    page_size = int(st.session_state.get("page_size", 1500))
    total_filtered = int(mask.sum())
    n_pages = max(1, (total_filtered + page_size - 1) // page_size)
    page = max(0, min(int(st.session_state.get("page", 0)), n_pages - 1))
    st.session_state["page"] = page

    _render_page_controls(page, n_pages, total_filtered, len(work))

    page_df, _, _ = sh.page_slice(work, mask, page, page_size)

    # ---- The editor.
    edited = _render_editor(page_df, work, loaded)

    # ---- Commit manual edits immediately (auto-save) with undo support.
    _commit_with_undo(work, edited, loaded, storage, config, rules)

    rc.render_target_account_prompt(work, loaded)

    # ---- Handle deferred toolbar actions (after edits are safely committed).
    _handle_actions(actions, work, loaded, config, rules, storage, mm, logger)

    # ---- Overlays (only one modal per run; confirmations take precedence so a
    # destructive action is never hidden behind another dialog).
    if st.session_state.get("confirm_action"):
        _confirm_dialog(work, loaded, config, rules, storage)
    elif st.session_state.get("export_open") and not st.session_state.get("panel"):
        _export_dialog(work, loaded, counts)
    elif st.session_state.get("rule_panel_open"):
        rc.rule_creation_dialog(work, loaded, config, rules, storage)


# --------------------------------------------------------------------------- #
# Confirmation dialog for destructive reset actions
# --------------------------------------------------------------------------- #
def _dismiss_confirm() -> None:
    st.session_state["confirm_action"] = None


def _do_clear_spreadsheet() -> None:
    """on_click handler — runs before widgets re-instantiate, so it may safely
    reset widget-keyed state."""
    st.session_state["confirm_action"] = None
    work = st.session_state.get("work_df")
    loaded = st.session_state.get("loaded")
    config = common.services()[0]
    if work is None or loaded is None:
        return
    common.push_undo()
    n = sh.clear_spreadsheet_values(work, loaded, config)
    _bump()
    common.set_flash(f"Cleared Target Account and Rule Notes on {n:,} row(s).")


def _do_start_fresh() -> None:
    """on_click handler — wipes persisted memory and unloads the file."""
    _, storage, rules, *_ = common.services()
    rules.clear_rules()
    storage.clear_learned_mappings()
    storage.clear_rule_notes()
    common.reset_file_session()
    common.set_flash("Cleared rules, learned mappings and Rule Notes. "
                     "Upload a file to begin again.")


@st.dialog("Please confirm", on_dismiss=_dismiss_confirm)
def _confirm_dialog(work, loaded, config, rules, storage) -> None:
    kind = st.session_state.get("confirm_action")
    if kind == "clear_spreadsheet":
        counts = sh.summary_counts(work, loaded)
        st.warning(
            f"This clears every **Target Account** and **Rule Notes** value in "
            f"this file ({counts['filled']:,} coded, {counts['with_notes']:,} "
            f"with notes). Your original uploaded columns are kept. Confidence "
            f"and review flags are reset. This cannot be undone.")
        st.caption("Your saved keyword rules and learned mappings are not "
                   "affected — only this file's editable values.")
        c1, c2 = st.columns([1.4, 1])
        c1.button("Clear spreadsheet data", type="primary", width="stretch",
                  key="confirm_clear_sheet_yes", on_click=_do_clear_spreadsheet)
        c2.button("Cancel", width="stretch", key="confirm_clear_sheet_no",
                  on_click=_dismiss_confirm)

    elif kind == "start_fresh":
        n_rules = len(rules.list_rules())
        n_maps = len(storage.list_learned_mappings())
        n_notes = storage.count_rule_notes()
        st.warning(
            "**Start fresh** permanently deletes everything below and unloads "
            "the current file:")
        st.markdown(
            f"- All keyword rules (**{n_rules}**)\n"
            f"- All learned mappings (**{n_maps}**)\n"
            f"- All saved Rule Notes (**{n_notes}**)\n"
            f"- The currently loaded file")
        st.caption("Trained models and run history are kept. This cannot be "
                   "undone.")
        c1, c2 = st.columns([1.4, 1])
        c1.button("Yes, start fresh", type="primary", width="stretch",
                  key="confirm_fresh_yes", on_click=_do_start_fresh)
        c2.button("Cancel", width="stretch", key="confirm_fresh_no",
                  on_click=_dismiss_confirm)


# --------------------------------------------------------------------------- #
# Header + metrics + incentive
# --------------------------------------------------------------------------- #
def _engine_status_text() -> str:
    """Compact 'what's loaded' chip for the spreadsheet header."""
    from src.ml_classifier import setfit_available
    from src.similarity import semantic_backend_available

    config, _, _, mm, _ = services()
    sem_ok, _ = semantic_backend_available()
    sf_ok, _ = setfit_available()
    matching = ("semantic AI" if (sem_ok and config.similarity.use_embeddings)
                else "TF-IDF")
    if mm.has_model():
        ml = "SetFit + LogReg" if sf_ok else "LogReg"
    else:
        ml = "warming up"
    return f"Matching: {matching} · ML: {ml}"


def _render_header(loaded, counts: dict) -> None:
    """Compact, single-strip header so the grid owns the viewport."""
    client = st.session_state.get("client_name") or ""
    title = "Spreadsheet" + (f" — {client}" if client else "")
    head = st.columns([3.2, 2.8])
    head[0].markdown(f"### {title}")
    head[0].caption(f"{loaded.source_name} · {counts['total']:,} transactions · "
                    + _engine_status_text())

    pct = (counts["filled"] / counts["total"]) if counts["total"] else 0.0
    head[1].progress(pct, text=f"{counts['filled']:,}/{counts['total']:,} "
                               f"coded · {pct:.0%}")
    head[1].caption(
        f"Filled {counts['filled']:,}  ·  Auto {counts['auto_filled']:,}  ·  "
        f"Review {counts['review_pending']:,}  ·  Blank {counts['blank']:,}  ·  "
        f"Examples {counts['seeds']:,}  ·  Selected {counts['selected']:,}")

    note = common.value_note(counts)
    if note:
        head[0].caption(note)


# --------------------------------------------------------------------------- #
# Toolbar (sticky) + automation controls
# --------------------------------------------------------------------------- #
def _render_toolbar(work, loaded, counts: dict, config) -> dict:
    st.markdown("<div class='pc-toolbar'></div>", unsafe_allow_html=True)
    actions = {}
    r1 = st.columns([2, 2, 2.4, 2, 1, 1, 1.4])
    actions["run_full"] = r1[0].button(
        "Full Intelligent Run", type="primary", width="stretch", key="tb_run_full",
        help="Group similar transactions and fill codes using rules, learned "
             "memory, similarity and the AI model.")
    actions["run_rules"] = r1[1].button(
        "Run Selected Rules", width="stretch", key="tb_run_rules",
        help="Apply keyword rules to the selected rows (or all blank rows when "
             "nothing is selected).")
    sel_count = counts["selected"]
    actions["create_rule"] = r1[2].button(
        "Create Rule from Selection", width="stretch", key="tb_create_rule",
        type="primary" if sel_count else "secondary",
        disabled=sel_count == 0,
        help="Select rows with the Sel checkbox to build a rule. You're also "
             "prompted automatically after typing a Target Account code.")
    actions["approve"] = r1[3].button(
        f"Approve Selected ({sel_count})", width="stretch",
        key="tb_approve", disabled=sel_count == 0,
        help="Confirm the codes on the selected rows and add them to training.")
    actions["undo"] = r1[4].button(
        "Undo", width="stretch", key="tb_undo",
        disabled=not st.session_state.get("undo_stack"))
    actions["redo"] = r1[5].button(
        "Redo", width="stretch", key="tb_redo",
        disabled=not st.session_state.get("redo_stack"))
    actions["export"] = r1[6].button(
        "Export", width="stretch", key="tb_export",
        help="Download the finished file (Excel preserves formatting; CSV is "
             "ready to import).")

    r2 = st.columns([2.2, 2, 2, 1.8, 2.2])
    with r2[0].popover("Automation settings", use_container_width=True):
        _render_automation_controls(config)
    actions["select_all"] = r2[1].button(
        "Select all in view", width="stretch", key="tb_select_all",
        help="Select every row that matches the current search and filters "
             "(across all pages).")
    actions["clear_sel"] = r2[2].button("Clear selection", width="stretch",
                                        key="tb_clear_sel")
    with r2[3].popover("Reset", use_container_width=True):
        _render_reset_controls()
    r2[4].caption(_automation_summary(config))
    return actions


def _render_reset_controls() -> None:
    """Safe reset actions; each opens a confirmation dialog before doing anything."""
    st.markdown("**Reset & clean up**")
    st.caption("Destructive actions — you'll be asked to confirm first.")
    if st.button("Clear spreadsheet data", width="stretch",
                 key="reset_clear_sheet",
                 help="Clear every Target Account and Rule Notes value in this "
                      "file. Your original columns are kept."):
        st.session_state["confirm_action"] = "clear_spreadsheet"
        st.session_state["export_open"] = False
        st.session_state["panel"] = None
        st.rerun()
    if st.button("Start fresh", width="stretch", key="reset_start_fresh",
                 help="Delete all rules, learned mappings and saved Rule Notes, "
                      "and unload the current file."):
        st.session_state["confirm_action"] = "start_fresh"
        st.session_state["export_open"] = False
        st.session_state["panel"] = None
        st.rerun()


def _render_automation_controls(config) -> None:
    """Intelligent-mode + semantic toggle, available right on the spreadsheet."""
    st.markdown("**Run options**")
    labels = list(common.ML_MODE_LABELS.values())
    keys = list(common.ML_MODE_LABELS.keys())
    cur = st.session_state.get("ml_mode", config.ml.mode)
    idx = keys.index(cur) if cur in keys else 0
    choice = st.selectbox(
        "Intelligent mode", labels, index=idx, key="tb_ml_mode_label",
        help="How the Full Intelligent Run decides codes.")
    st.session_state["ml_mode"] = keys[labels.index(choice)]

    available, _ = semantic_backend_available()
    config.similarity.use_embeddings = st.toggle(
        "Use Semantic AI matching",
        value=config.similarity.use_embeddings and available,
        disabled=not available, key="tb_semantic",
        help=("Matches on meaning rather than spelling." if available else
              "Optional engine not installed — TF-IDF is used."))
    if not available:
        st.caption("Semantic AI not installed — add it from the sidebar to "
                   "enable meaning-based matching.")


def _automation_summary(config) -> str:
    mode_label = common.ML_MODE_LABELS.get(
        st.session_state.get("ml_mode", config.ml.mode), "")
    mode_short = mode_label.split("—")[0].strip() or mode_label
    matching = "Semantic AI" if config.similarity.use_embeddings else "TF-IDF"
    return f"Mode: {mode_short} · Matching: {matching}"


# --------------------------------------------------------------------------- #
# Filter bar — global search across ALL records + quick views
# --------------------------------------------------------------------------- #
def _current_mask(work, loaded, config):
    """Build the active view mask from the search/filter widgets."""
    scope = st.session_state.get("search_scope", "All columns")
    cols = None if scope == "All columns" else [scope]
    engines = st.session_state.get("engine_filter") or None
    return sh.build_view_mask(
        work, loaded,
        mode=st.session_state.get("filter_mode") or "All",
        query=st.session_state.get("search_query", ""),
        search_columns=cols,
        engines=engines,
        cutoff=float(config.confidence.auto_apply_cutoff))


def _clear_filters() -> None:
    st.session_state["filter_mode"] = "All"
    st.session_state["search_query"] = ""
    st.session_state["search_scope"] = "All columns"
    st.session_state["engine_filter"] = []
    st.session_state["page"] = 0


def _render_filter_bar(work, loaded, config):
    # Quick-view toggles: Show All / Review only / High confidence / Blanks / Filled.
    st.segmented_control(
        "Quick view", FILTER_MODES, key="filter_mode",
        selection_mode="single", label_visibility="collapsed")

    f = st.columns([3.4, 1.9, 2.3, 1.2, 1.2])
    f[0].text_input(
        "Search", key="search_query", label_visibility="collapsed",
        placeholder="Search all records — vendor, memo, code, notes…")
    scope_opts = ["All columns"] + sh.searchable_columns(work, loaded)
    if st.session_state.get("search_scope") not in scope_opts:
        st.session_state["search_scope"] = "All columns"
    f[1].selectbox("Search in", scope_opts, key="search_scope",
                   label_visibility="collapsed")

    eng_opts = sh.available_engines(work)
    # Drop any stale selections no longer present so the widget never errors.
    st.session_state["engine_filter"] = [
        e for e in st.session_state.get("engine_filter", []) if e in eng_opts]
    f[2].multiselect("Decided by", eng_opts, key="engine_filter",
                     label_visibility="collapsed",
                     placeholder="Filter by how decided")
    f[3].selectbox("Rows/page", PAGE_SIZES, key="page_size",
                   label_visibility="collapsed")
    # Reset via on_click so widget-keyed state is changed *before* the widgets
    # are re-instantiated on the next run (Streamlit forbids mid-run changes).
    f[4].button("Clear filters", width="stretch", key="tb_clear_filters",
                on_click=_clear_filters)

    mask = _current_mask(work, loaded, config)

    # Reset to the first page whenever the view changes (keeps paging sane).
    sig = (st.session_state.get("filter_mode"),
           st.session_state.get("search_query"),
           st.session_state.get("search_scope"),
           tuple(st.session_state.get("engine_filter") or ()),
           st.session_state.get("page_size"))
    if st.session_state.get("_view_sig") != sig:
        st.session_state["_view_sig"] = sig
        st.session_state["page"] = 0
    return mask


def _render_page_controls(page: int, n_pages: int, total_filtered: int,
                          total_all: int) -> None:
    c = st.columns([1, 1, 5, 2])
    if c[0].button("Previous", disabled=page <= 0, width="stretch", key="pg_prev"):
        st.session_state["page"] = max(0, page - 1)
        st.rerun()
    if c[1].button("Next", disabled=page >= n_pages - 1, width="stretch",
                   key="pg_next"):
        st.session_state["page"] = min(n_pages - 1, page + 1)
        st.rerun()
    if total_filtered != total_all:
        match_txt = f"**{total_filtered:,}** of {total_all:,} records match"
    else:
        match_txt = f"All **{total_all:,}** records"
    c[2].caption(f"{match_txt} · page {page + 1} / {n_pages}")


# --------------------------------------------------------------------------- #
# Editor
# --------------------------------------------------------------------------- #
def _render_editor(page_df, work, loaded):
    na_col = loaded.new_account_col
    cols = sh.editor_columns(loaded, work)
    view = page_df[cols].copy()

    suggestions = _code_suggestions(work, na_col)
    column_config = {
        sh.SELECT_COL: st.column_config.CheckboxColumn(
            "Sel", help="Select rows for bulk actions and rule creation.",
            width="small"),
        na_col: st.column_config.TextColumn(
            sh.TARGET_ACCOUNT_LABEL,
            help="The account / category code. After you enter a code, you'll be "
                 "offered a one-click rule. Suggestions: "
                 + (", ".join(suggestions[:8]) if suggestions else "—")),
        RULE_NOTES_COL: st.column_config.TextColumn(
            "Rule Notes",
            help="Your hints about this transaction. These improve matching and "
                 "are remembered for future files."),
        sh.CONF_COL: st.column_config.ProgressColumn(
            "Confidence", min_value=0.0, max_value=1.0, format="%.0f%%"),
        sh.ENGINE_COL: st.column_config.TextColumn("How decided"),
    }
    disabled = [c for c in view.columns
                if c not in (sh.SELECT_COL, na_col, RULE_NOTES_COL)]
    version = st.session_state.get("data_version", 0)
    page = st.session_state.get("page", 0)
    # The key must change whenever the displayed slice changes (page, filters,
    # search) so stale edit deltas are never replayed onto different rows.
    sig = st.session_state.get("_view_sig")
    key = f"grid_{version}_{page}_{abs(hash(sig)) & 0xffffffff}"
    return st.data_editor(
        view, width="stretch", height=EDITOR_HEIGHT, column_config=column_config,
        disabled=disabled, hide_index=True, key=key)


def _code_suggestions(work, na_col) -> list:
    """Most common existing codes — used as free-text hints."""
    vals = (work[na_col].astype(str).str.strip())
    vals = vals[vals != ""]
    return list(vals.value_counts().index[:12])


def _commit_with_undo(work, edited, loaded, storage, config, rules) -> None:
    pre = sh.snapshot(work)
    counts = sh.commit_editor_changes(work, edited, loaded, storage, config)
    if counts.get("targets") or counts.get("notes"):
        stack = st.session_state["undo_stack"]
        stack.append(pre)
        if len(stack) > 30:
            del stack[0]
        st.session_state["redo_stack"] = []
    rc.queue_target_edit_prompts(counts.get("target_edits", []), rules)


# --------------------------------------------------------------------------- #
# Action handling
# --------------------------------------------------------------------------- #
def _handle_actions(actions, work, loaded, config, rules, storage, mm, logger) -> None:
    # Undo / redo.
    if actions.get("undo"):
        if common.undo():
            _bump()
            common.set_flash("Undid the last change.")
        st.rerun()
    if actions.get("redo"):
        if common.redo():
            _bump()
            common.set_flash("Redid the change.")
        st.rerun()

    # Selection helpers — "select all in view" respects search + filters and
    # spans every page of the current view, not just the one on screen.
    if actions.get("select_all"):
        mask = _current_mask(work, loaded, config)
        sh.set_selection(work, [int(i) for i in work.index[mask]], True)
        _bump()
        n = int(mask.sum())
        common.set_flash(f"Selected {n:,} row(s) matching the current view.")
        st.rerun()
    if actions.get("clear_sel"):
        sh.clear_selection(work)
        _bump()
        st.rerun()

    # Full intelligent run.
    if actions.get("run_full"):
        common.push_undo()
        progress = st.progress(0.0, text="Starting…")

        def cb(frac, msg):
            progress.progress(min(max(frac, 0.0), 1.0), text=msg)

        try:
            with st.spinner("Running the full automation…"):
                result = sh.run_full(work, loaded, config, common.make_engine(),
                                     progress_cb=cb)
            st.session_state["last_result"] = result
            _record_run(result, storage)
            progress.progress(1.0, text="Done.")
            counts = sh.summary_counts(work, loaded)
            msg = (f"Filled {counts['filled']:,} transactions "
                   f"({counts['auto_filled']:,} automatically). "
                   f"{counts['review_pending']:,} flagged for review.")
            note = common.value_note(counts)
            if note:
                msg += f"  {note}."
            common.set_flash(msg)
            _bump()
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"The run did not complete: {exc}")
            logger.error("run_failed", error=str(exc))

    # Rules-only run. An explicit selection signals intent to (re)apply rules to
    # those rows, overriding earlier engine guesses (seeds/manual stay safe).
    # With nothing selected we fill blank rows only.
    if actions.get("run_rules"):
        common.push_undo()
        sel = sh.selected_indices(work)
        indices = sel if sel else None
        n = sh.run_rules_only(work, rules, loaded, config, indices=indices,
                              overwrite=bool(sel))
        _bump()
        if not rules.list_rules(enabled_only=True):
            common.set_flash(
                "No keyword rules yet. Create one from a selection, or accept a "
                "suggested rule in the Rules panel.")
        else:
            common.set_flash(
                f"Applied keyword rules to {n} "
                f"{'selected ' if sel else 'blank '}row(s).")
        st.rerun()

    # Open the rule-creation dialog, pre-filled from the checkbox selection.
    if actions.get("create_rule"):
        sel = sh.selected_indices(work)
        if not sel:
            common.set_flash(
                "Select one or more rows with the Sel checkbox to create a rule.")
            st.rerun()
        prefill = sh.rule_creation_prefill(work, sel, loaded,
                                           rules_manager=rules)
        rc.open_rule_panel(prefill)
        st.rerun()

    # Open the export modal (closing any management panel first — one modal/run).
    if actions.get("export"):
        st.session_state["export_open"] = True
        st.session_state["panel"] = None
        st.rerun()

    # Approve selected.
    if actions.get("approve"):
        common.push_undo()
        out = sh.approve_rows(work, loaded, storage, config)
        _bump()
        common.set_flash(
            f"Approved {out['applied']} transaction(s); learned "
            f"{out['learned']} mapping(s).")
        st.rerun()


# --------------------------------------------------------------------------- #
# Run history record
# --------------------------------------------------------------------------- #
def _record_run(result, storage) -> None:
    try:
        client = st.session_state.get("client_name") or ""
        if client:
            result.summary.file_name = f"{client}: {result.summary.file_name}"
        storage.add_run(result.summary)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Downloads
# --------------------------------------------------------------------------- #
def _close_export() -> None:
    st.session_state["export_open"] = False


@st.dialog("Export", width="large", on_dismiss=_close_export)
def _export_dialog(work, loaded, counts: dict) -> None:
    st.caption("Internal helper columns are stripped automatically; each Excel "
               "file includes a 'Fill Down Summary' tab.")
    base_msg = (f"{counts['filled']:,} of {counts['total']:,} transactions are "
                "coded — ready for journal entries and clean reporting.")
    note = common.value_note(counts)
    common.incentive(base_msg + (f" {note}." if note else ""))

    config, *_ = services()
    na_col = loaded.new_account_col
    from pathlib import Path
    stem = Path(loaded.source_name).stem
    result = st.session_state.get("last_result")
    backend = result.backend if result else ""
    mode = result.mode if result else ""
    summary_df = common.export_summary_df(counts, loaded, backend, mode)

    ext = st.session_state.get("original_ext")
    orig = st.session_state.get("original_bytes")
    try:
        with st.spinner("Preparing your files…"):
            if orig and ext in (".xlsx", ".xlsm", ".xls"):
                xlsx = export_preserving_original(
                    orig, work, na_col, config,
                    extra_header_candidates=[loaded.original_new_account_header],
                    summary=summary_df)
                label = "Excel (keeps your formatting)"
            else:
                xlsx = export_dataframe(work, fmt="xlsx", summary=summary_df)
                label = "Excel (tidy)"
            qb_df = work.drop(columns=[RULE_NOTES_COL], errors="ignore")
            qb_bytes = export_csv(qb_df, na_col)
            clean_bytes = export_dataframe(work, fmt="xlsx", summary=summary_df)

        c1, c2, c3 = st.columns(3)
        c1.download_button(
            label, data=xlsx, file_name=f"filled_{stem}.xlsx",
            mime="application/vnd.openxmlformats-officedocument."
                 "spreadsheetml.sheet", width="stretch")
        c2.download_button(
            "CSV (ready to import)", data=qb_bytes,
            file_name=f"filled_{stem}_export.csv", mime="text/csv",
            width="stretch")
        c3.download_button(
            "Excel (all columns + notes)", data=clean_bytes,
            file_name=f"filled_{stem}_clean.xlsx",
            mime="application/vnd.openxmlformats-officedocument."
                 "spreadsheetml.sheet", width="stretch")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not build the download: {exc}")

    if st.button("Close", key="export_close", width="stretch"):
        _close_export()
        st.rerun()
