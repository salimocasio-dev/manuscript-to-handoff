"""Local editorial review and production handoff, with an explicit export gate."""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from mth.editorial import EditorialError, SAMPLE_TEXT, default_model, live_review, recorded_review
from mth.handoff import ExportBlocked, build_candidate, corrupt_candidate, export_package
from mth.store import Store, WorkflowError
from mth.validation import candidate_hash, validate_candidate


st.set_page_config(page_title="Manuscript to Handoff", page_icon="📖", layout="wide")
st.markdown(
    """<style>
    .block-container {max-width: 1240px; padding-bottom: 3rem;}
    h1, h2, h3 {color: #203344; letter-spacing: -.025em;}
    h1 {font-family: Georgia, serif !important; font-weight: 500 !important;}
    h3 {font-size: 1.28rem !important;}
    [data-testid="stCaptionContainer"] {color: #596671;}
    [data-testid="stTabs"] button {font-size: .95rem;}
    [data-testid="stCode"] {font-size: .93rem;}
    .eyebrow {font-size: .72rem; font-weight: 700; letter-spacing: .14em;
              color: #176D73; margin-bottom: .25rem;}
    .rule {height: 1px; background: #dcded7; margin: 1rem 0 1.35rem;}
    </style>""",
    unsafe_allow_html=True,
)

def environment_flag(name: str) -> bool:
    """Treat only explicit truthy values as permission for an optional feature."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


configured_db_path = os.environ.get("MTH_DB_PATH", "").strip()
EPHEMERAL_WORKSPACE = not configured_db_path
LIVE_AI_ENABLED = environment_flag("MTH_ENABLE_LIVE_AI")

if EPHEMERAL_WORKSPACE:
    if not isinstance(st.session_state.get("_mth_store"), Store):
        st.session_state["_mth_store"] = Store(":memory:")
    store = st.session_state["_mth_store"]
else:
    store = Store(Path(configured_db_path).expanduser())


def flash(message: str) -> None:
    st.session_state["notice"] = message
    st.rerun()


def failure(error: Exception) -> None:
    st.error(str(error))


def candidate_contents(record: dict | None) -> dict | None:
    """Database records carry the artifact separately from their storage ID."""
    if record is None:
        return None
    return record.get("candidate", record)


def report_view(report: dict) -> None:
    if report.get("passed"):
        st.success("Validation passed — approved text is preserved.")
    else:
        st.error("Export blocked — the candidate failed validation.")
    for error in report.get("errors", []):
        st.write(f"✕ **{error.get('code', 'Check')}** — {error.get('message', '')}")
        location = {key: value for key, value in error.items() if key not in ("code", "message")}
        if location:
            st.json(location, expanded=False)
    with st.expander("View every validation check"):
        for check in report.get("checks", []):
            symbol = "✓" if check.get("passed") else "✕"
            st.write(f"{symbol} **{check.get('code', 'Check')}** — {check.get('message', '')}")
    for issue in report.get("warnings", []):
        st.caption(f"Nonblocking issue: {issue.get('message', issue) if isinstance(issue, dict) else issue}")


def report_bytes(report: dict) -> bytes:
    return (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


latest = store.latest_revision()
revisions = store.list_revisions()
approval = store.approval_for(latest["id"]) if latest else None
suggestions = store.list_suggestions(latest["id"]) if latest else []
candidate_record = store.latest_candidate()
candidate = candidate_contents(candidate_record)
candidate_id = candidate_record["id"] if candidate_record else None
validations = store.list_validations(candidate_id) if candidate_id is not None else []
last_validation = validations[-1] if validations else None
report_bound_to_candidate = bool(
    candidate and last_validation
    and last_validation["report"].get("candidate_hash") == candidate_hash(candidate)
)
candidate_is_current = bool(
    candidate and latest and candidate.get("revision_id") == latest["id"]
    and candidate.get("content_hash") == latest["content_hash"]
)

st.markdown('<div class="eyebrow">A PUBLISHING OPERATIONS PROTOTYPE</div>', unsafe_allow_html=True)
st.title("Manuscript to Handoff")
st.write("AI-assisted publishing operations with human approval and verifiable production handoffs.")
st.caption("AI suggests. A person approves an exact revision. Ordinary code checks the text that leaves the workflow.")
st.markdown('<div class="rule"></div>', unsafe_allow_html=True)

if EPHEMERAL_WORKSPACE:
    notice_col, reset_col = st.columns([5, 1], vertical_alignment="center")
    with notice_col:
        st.info(
            "Temporary demo session — your data is isolated to this browser session and is not written to disk. "
            "It disappears when the session ends or you reset it. Do not paste confidential manuscript text."
        )
    with reset_col:
        if st.button("Reset demo session", use_container_width=True):
            store.close()
            st.session_state.clear()
            st.session_state["workflow_tab"] = "Manuscript"
            st.rerun()

if notice := st.session_state.pop("notice", None):
    st.success(notice)

if latest is None:
    stage, next_action = "1 · Start with a manuscript", "Load the fictional sample to walk through the demo, or add your own plain text."
elif not approval:
    if suggestions and any(item.get("decision") == "pending" for item in suggestions):
        stage, next_action = "2 · Review the draft", "Accept or reject each editorial suggestion, then separately approve the current revision."
    elif suggestions:
        stage, next_action = "2 · Approve the reviewed manuscript", "Your editorial decisions are saved. Review the text in Approval and approve this exact revision."
    elif latest["reason"] == "accepted_suggestions":
        stage, next_action = "2 · Approve the revised manuscript", "Accepted changes created a fresh draft. Review the updated text in Approval and approve this exact revision."
    else:
        stage, next_action = "2 · Review the draft", "Generate editorial suggestions, or review the text yourself and approve this revision."
elif candidate is None:
    stage, next_action = "3 · Build the handoff", "Create a production candidate from the approved revision, then run the independent validator."
elif last_validation is None or not report_bound_to_candidate:
    stage, next_action = "4 · Validate the candidate", "Run validation. A generated candidate cannot be exported until its contents pass."
elif not last_validation["report"].get("passed"):
    stage, next_action = "4 · Correct the blocked handoff", "Inspect the failed checks, rebuild from the approved revision, then run validation again."
else:
    # A new current revision can never inherit an old candidate's successful gate.
    current_check = validate_candidate(candidate, latest, approval)
    if current_check.get("passed"):
        stage, next_action = "5 · Handoff ready", "Download the verified package, or introduce a demo error to see the export gate work."
    else:
        stage, next_action = "3 · Rebuild for the current revision", "The saved candidate belongs to earlier work. Rebuild and validate against the current approval."

with st.container(border=True):
    st.subheader(stage)
    st.write(next_action)
    if latest:
        st.caption(f"Current revision: {latest['id']} · {'Approved' if approval else 'Draft — not approved'} · SHA-256: {latest['content_hash'][:16]}…")

manuscript_tab, review_tab, approval_tab, handoff_tab, evidence_tab = st.tabs(
    ["Manuscript", "Edit & review", "Approval", "Handoff", "Evidence & history"],
    key="workflow_tab",
    on_change="rerun",
)

with manuscript_tab:
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("The working manuscript")
        st.caption("Each save creates a distinct draft. Earlier wording and line breaks remain in revision history.")
        if st.button("Load sample manuscript", type="primary" if latest is None else "secondary"):
            try:
                revision = store.create_revision(SAMPLE_TEXT, parent_id=latest["id"] if latest else None, reason="load_sample")
                flash(f"Fictional sample loaded as draft {revision['id']}. Existing history is preserved.")
            except WorkflowError as error:
                failure(error)
        editor_text = st.text_area(
            "Manuscript text", value=latest["text"] if latest else "", height=330,
            key=f"manuscript_text_{latest['id'] if latest else 'empty'}",
            placeholder="Paste a manuscript here, including its line breaks…",
        )
        if st.button("Save new revision"):
            try:
                if not editor_text:
                    st.error("Enter manuscript text before saving.")
                elif latest and editor_text == latest["text"]:
                    st.info("There are no text changes to save.")
                else:
                    revision = store.create_revision(editor_text, parent_id=latest["id"] if latest else None, reason="manual")
                    flash(f"Saved draft {revision['id']}. It needs its own approval.")
            except WorkflowError as error:
                failure(error)
        with st.expander("Import a UTF-8 text file"):
            uploaded = st.file_uploader("Manuscript .txt file", type=["txt"])
            st.caption("Import preserves file content exactly, including CRLF line endings. Browser text editing uses the text submitted by your browser.")
            if st.button("Import text as new revision", disabled=uploaded is None):
                try:
                    imported = uploaded.getvalue().decode("utf-8")
                    revision = store.create_revision(imported, parent_id=latest["id"] if latest else None, reason="file_upload")
                    flash(f"Imported draft {revision['id']} without text normalization.")
                except (UnicodeDecodeError, WorkflowError) as error:
                    failure(error)
    with right:
        st.subheader("A five-minute walkthrough")
        st.markdown("1. Load the fictional sample.\n2. Generate the recorded suggestions and save your decisions.\n3. Approve the current revision.\n4. Build the handoff and run validation.\n5. Remove a manuscript line, validate the failure, then rebuild and validate again.")
        st.info("The sample and editorial fixture were purpose-written for this prototype. No unpublished manuscript or private artwork is included.")
        if latest:
            st.caption(f"Revision created: {latest['created_at']}\n\nSource: {latest['reason']}")

with review_tab:
    if not latest:
        st.info("Load or save a manuscript to begin editorial review.")
    else:
        st.subheader("Editorial judgment stays reviewable")
        st.caption(f"Suggestions are attached to revision {latest['id']}. Accepting a change creates a new draft; it does not approve it.")
        source_col, suggestion_col = st.columns([1, 1], gap="large")
        with source_col:
            st.markdown("**Current manuscript**")
            st.code(latest["text"], language=None, wrap_lines=True)
        with suggestion_col:
            if not LIVE_AI_ENABLED and st.session_state.get("editorial_mode") == "Live model":
                del st.session_state["editorial_mode"]
            editorial_modes = ["Recorded demo (authored fixture; no API call)"]
            if LIVE_AI_ENABLED:
                editorial_modes.append("Live model")
            mode = st.radio("Editorial mode", editorial_modes, key="editorial_mode")
            is_live = mode == "Live model"
            consent = False
            if is_live:
                st.caption(f"Provider: OpenAI · Model: {default_model()}")
                if not os.environ.get("OPENAI_API_KEY", "").strip():
                    st.warning("Live review is unavailable: set OPENAI_API_KEY in your environment, then restart the app.")
                consent = st.checkbox("I agree to send this manuscript to OpenAI for editorial review.", key=f"provider_consent_{latest['id']}")
            else:
                st.caption("This authored example runs locally. It works only on the exact included sample and makes no claim of a live model response.")
                if not LIVE_AI_ENABLED:
                    st.caption("Live AI is disabled by default. Set MTH_ENABLE_LIVE_AI=1 locally to make the provider option available.")
            can_generate = not is_live or (
                LIVE_AI_ENABLED and bool(os.environ.get("OPENAI_API_KEY", "").strip()) and consent
            )
            if st.button("Generate suggestions", disabled=not can_generate):
                try:
                    if is_live and not LIVE_AI_ENABLED:
                        raise EditorialError("Live AI is disabled. Set MTH_ENABLE_LIVE_AI=1 before starting the app.")
                    with st.spinner("Requesting live editorial suggestions…" if is_live else "Loading the authored fixture…"):
                        proposed = live_review(latest) if is_live else recorded_review(latest)
                    store.save_suggestions(latest["id"], proposed, mode="live" if is_live else "recorded", provider_model=default_model() if is_live else "authored_fixture")
                    if is_live and not proposed:
                        flash("The live model returned no suggestions. Review the manuscript yourself before approval.")
                    else:
                        flash("Live suggestions saved for human review." if is_live else "Recorded editorial fixture loaded. No model was called.")
                except (EditorialError, WorkflowError) as error:
                    failure(error)
            if suggestions:
                choices = {}
                decision_names = {"accepted": "Accept", "rejected": "Reject"}
                for index, suggestion in enumerate(suggestions, start=1):
                    with st.container(border=True):
                        st.markdown(f"**Suggestion {index}**")
                        st.caption(f"Source: {suggestion['mode']} · Match: {suggestion['match_status']}")
                        st.markdown("Original passage")
                        st.code(suggestion["original"], language=None, wrap_lines=True)
                        st.markdown("Proposed replacement")
                        st.code(suggestion["replacement"], language=None, wrap_lines=True)
                        st.write(suggestion["rationale"])
                        selected = decision_names.get(suggestion.get("decision"), "Pending")
                        choices[suggestion["id"]] = st.radio(
                            f"Suggestion {index} decision", ["Pending", "Accept", "Reject"],
                            index=["Pending", "Accept", "Reject"].index(selected), horizontal=True,
                            key=f"decision_{suggestion['id']}", disabled=selected != "Pending",
                        )
                undecided = [s for s in suggestions if s.get("decision") not in ("accepted", "rejected")]
                if st.button("Save review decisions", type="primary", disabled=not undecided):
                    if any(choices[s["id"]] == "Pending" for s in undecided):
                        st.error("Choose Accept or Reject for every pending suggestion before saving.")
                    else:
                        try:
                            decisions = {s["id"]: {"Accept": "accepted", "Reject": "rejected"}[choices[s["id"]]] for s in undecided}
                            revised = store.apply_decisions(latest["id"], decisions)
                            changed = revised["id"] != latest["id"]
                            flash(f"Decisions saved. Accepted changes created draft {revised['id']}; approval is still separate." if changed else "Rejections saved. The manuscript is unchanged.")
                        except WorkflowError as error:
                            failure(error)

with approval_tab:
    st.subheader("Approve one exact revision")
    if not latest:
        st.info("Load or save a manuscript before approval.")
    else:
        st.code(latest["text"], language=None, wrap_lines=True)
        st.caption(f"Revision: {latest['id']}\n\nFull SHA-256: {latest['content_hash']}")
        if approval:
            st.success("This revision has an explicit human approval record.")
            st.json(approval, expanded=False)
        else:
            reviewer = st.text_input("Reviewer name", value="Local reviewer", key="reviewer_name")
            confirm = st.checkbox("I approve this exact revision for the production handoff.", key=f"approve_confirm_{latest['id']}")
            if st.button("Approve current revision", type="primary", disabled=not confirm):
                try:
                    if not reviewer.strip():
                        st.error("Enter a reviewer name to record the approval.")
                    else:
                        store.approve(latest["id"], actor=reviewer.strip())
                        flash(f"Revision {latest['id']} approved. You can now build its handoff.")
                except WorkflowError as error:
                    failure(error)
        st.caption("This local reviewer name is not authenticated enterprise identity. Content hashes detect text mismatches; they do not make the local database tamper-proof.")

with handoff_tab:
    st.subheader("A handoff that must prove its contents")
    st.caption("Four ordered spread allocations demonstrate controlled text transfer. They are not a professional pagination or print-readiness decision.")
    if not approval:
        st.warning("Export blocked — the current revision needs explicit human approval.")
    build_col, validate_col = st.columns(2)
    with build_col:
        if st.button("Build handoff", disabled=not approval, type="primary" if candidate is None else "secondary"):
            try:
                store.save_candidate(build_candidate(latest, approval, spread_count=4))
                flash("Handoff candidate saved. Run validation before export.")
            except (WorkflowError, ValueError) as error:
                failure(error)
    with validate_col:
        if st.button("Run validation", disabled=candidate is None or latest is None, type="primary" if candidate else "secondary"):
            try:
                report = validate_candidate(candidate, latest, approval)
                store.save_validation(candidate_id, report)
                flash("Validation passed. The package can now be exported." if report["passed"] else "Validation failed. Final handoff export is blocked; inspect the report below.")
            except (WorkflowError, ValueError) as error:
                failure(error)
    package = None
    if candidate_is_current and approval and report_bound_to_candidate and last_validation["report"].get("passed"):
        try:
            # A persisted pass is necessary; export also independently rechecks both
            # the current candidate and the actual ZIP before yielding any bytes.
            package = export_package(candidate, latest, approval)
        except ExportBlocked:
            st.error("Export blocked — the candidate no longer matches the current approved revision. Rebuild and validate again.")
        except (WorkflowError, ValueError) as error:
            failure(error)
    if candidate is not None:
        st.caption(f"Candidate record: {candidate_id}")
        if last_validation:
            if last_validation["report"].get("passed") and not (candidate_is_current and approval and report_bound_to_candidate):
                st.warning("The saved pass belongs to earlier work. The current revision needs a fresh approved candidate and validation.")
            else:
                report_view(last_validation["report"])
            st.download_button("Download validation report", data=report_bytes(last_validation["report"]), file_name="validation-report.json", mime="application/json")
        else:
            st.info("This candidate has no validation result yet. Export is blocked until you run validation.")
        with st.expander("Inspect the actual candidate contents"):
            st.json(candidate)
    st.download_button(
        "Download final handoff", data=package if package is not None else b"",
        file_name="manuscript-handoff.zip", mime="application/zip", disabled=package is None,
        type="primary",
    )
    st.caption("The ZIP includes source text, spread allocation, production notes, approval, validation, and a file-hash manifest. A pass proves the checked text-integrity conditions, not editorial or artistic quality.")
    if candidate is not None:
        with st.container(border=True):
            st.markdown("**Try a real production error**")
            st.caption("These controls change the saved candidate artifact. The approved manuscript remains intact; the ordinary validator evaluates the changed contents.")
            if st.button("Remove a manuscript line"):
                try:
                    store.save_candidate(corrupt_candidate(candidate, "missing_line"))
                    flash("A manuscript line was removed from a new candidate. Run validation to inspect the real failure.")
                except (WorkflowError, ValueError) as error:
                    failure(error)
            earlier = [revision for revision in revisions if revision["id"] != latest["id"]]
            if earlier:
                old_revision_id = st.selectbox("Earlier revision for demo", options=[revision["id"] for revision in earlier])
                if st.button("Use earlier revision"):
                    try:
                        old_revision = store.get_revision(old_revision_id)
                        store.save_candidate(corrupt_candidate(candidate, "wrong_revision", wrong_revision=old_revision))
                        flash("A new candidate now uses an earlier revision. Run validation against the current approval.")
                    except (WorkflowError, ValueError) as error:
                        failure(error)
            else:
                st.caption("The wrong-revision demo becomes available once you have a second revision.")
            if st.button("Rebuild from approved revision", disabled=not approval):
                try:
                    store.save_candidate(build_candidate(latest, approval, spread_count=4))
                    flash("Clean candidate rebuilt from the approved revision. Run validation again to reopen export.")
                except (WorkflowError, ValueError) as error:
                    failure(error)

with evidence_tab:
    st.subheader("History is part of the handoff")
    if EPHEMERAL_WORKSPACE:
        st.caption(
            "Revisions, decisions, approvals, candidates, and validation outcomes stay in this temporary browser session only."
        )
    else:
        st.caption(
            "Revisions, decisions, approvals, candidates, and validation outcomes persist in the configured local SQLite database across app restarts."
        )
    if revisions:
        st.markdown("**Manuscript revisions**")
        for revision in reversed(revisions):
            historical_approval = store.approval_for(revision["id"])
            status = "Approved" if historical_approval else "Draft"
            current_label = " · current" if latest["id"] == revision["id"] else ""
            with st.expander(f"{revision['id']} · {status}{current_label} · {revision['reason']}"):
                st.caption(f"Created {revision['created_at']} · Parent: {revision['parent_id'] or 'none'}")
                st.code(revision["text"], language=None, wrap_lines=True)
                st.caption(f"SHA-256: {revision['content_hash']}")
                historic_suggestions = store.list_suggestions(revision["id"])
                if historic_suggestions:
                    st.markdown("**Editorial suggestions and saved decisions**")
                    st.json(historic_suggestions, expanded=False)
                if historical_approval:
                    st.markdown("**Approval record**")
                    st.json(historical_approval, expanded=False)
        st.markdown("**Validation history**")
        all_validations = store.list_validations()
        if not all_validations:
            st.caption("No validation runs yet.")
        for validation_record in reversed(all_validations):
            label = "PASS" if validation_record["report"].get("passed") else "BLOCKED"
            with st.expander(f"{label} · candidate {validation_record['candidate_id']} · {validation_record['created_at']}"):
                report_view(validation_record["report"])
                st.json(validation_record["report"], expanded=False)
        with st.expander("Candidate artifact history"):
            st.json(store.list_candidates(), expanded=False)
    else:
        st.info("Your first saved manuscript will start the history.")
