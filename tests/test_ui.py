"""Exercise the actual Streamlit application, not a duplicate of its workflow."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import mth.editorial as editorial
from mth.editorial import SAMPLE_TEXT
from mth.store import Store


APP = Path(__file__).resolve().parents[1] / "app.py"


def button(app, label):
    return next(item for item in app.button if item.label == label)


def download(app, label):
    return next(item for item in app.get("download_button") if item.proto.label == label)


def click(app, label):
    button(app, label).click().run()
    assert not app.exception, [error.message for error in app.exception]
    return app


def approve(app):
    next(item for item in app.checkbox if item.label == "I approve this exact revision for the production handoff.").check().run()
    click(app, "Approve current revision")


@pytest.fixture
def ui(tmp_path, monkeypatch):
    db_path = tmp_path / "ui.sqlite3"
    monkeypatch.setenv("MTH_DB_PATH", str(db_path))
    monkeypatch.delenv("MTH_ENABLE_LIVE_AI", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    assert not app.exception
    return app, Store(db_path)


def reviewed_sample(app):
    click(app, "Load sample manuscript")
    click(app, "Generate suggestions")
    decisions = [item for item in app.radio if item.label.startswith("Suggestion ")]
    assert len(decisions) == 2
    decisions[0].set_value("Accept")
    decisions[1].set_value("Reject")
    click(app, "Save review decisions")


def test_ui_successful_workflow_and_restart_persistence(ui):
    app, store = ui
    reviewed_sample(app)
    revisions = store.list_revisions()
    assert len(revisions) == 2
    assert revisions[0]["text"] == SAMPLE_TEXT
    assert revisions[1]["text"] != SAMPLE_TEXT
    assert store.approval_for(revisions[1]["id"]) is None
    assert [item["decision"] for item in store.list_suggestions(revisions[0]["id"])] == ["accepted", "rejected"]
    assert button(app, "Build handoff").disabled
    approve(app)
    click(app, "Build handoff")
    assert download(app, "Download final handoff").proto.disabled
    click(app, "Run validation")
    assert store.list_validations()[-1]["report"]["passed"]
    assert not download(app, "Download final handoff").proto.disabled
    reloaded = AppTest.from_file(str(APP), default_timeout=10).run()
    assert not reloaded.exception
    assert not download(reloaded, "Download final handoff").proto.disabled
    assert "Handoff ready" in " ".join(item.value for item in reloaded.subheader)
    assert len(store.list_revisions()) == 2
    assert [item["decision"] for item in store.list_suggestions(revisions[0]["id"])] == ["accepted", "rejected"]


def test_ui_missing_line_blocks_export_then_recovery_requires_validation(ui):
    app, store = ui
    click(app, "Load sample manuscript")
    approve(app)
    approved = store.latest_revision()
    click(app, "Build handoff")
    click(app, "Run validation")
    assert not download(app, "Download final handoff").proto.disabled
    click(app, "Remove a manuscript line")
    assert len(store.list_candidates()) == 2
    assert download(app, "Download final handoff").proto.disabled
    click(app, "Run validation")
    assert not store.list_validations()[-1]["report"]["passed"]
    assert any("Export blocked" in item.value for item in app.error)
    assert download(app, "Download final handoff").proto.disabled
    assert not download(app, "Download validation report").proto.disabled
    assert store.latest_revision() == approved
    click(app, "Rebuild from approved revision")
    assert download(app, "Download final handoff").proto.disabled
    click(app, "Run validation")
    assert not download(app, "Download final handoff").proto.disabled
    assert [record["report"]["passed"] for record in store.list_validations()] == [True, False, True]


def test_ui_wrong_revision_and_subsequent_edit_cannot_reuse_old_export(ui):
    app, store = ui
    reviewed_sample(app)
    approve(app)
    click(app, "Build handoff")
    click(app, "Run validation")
    click(app, "Use earlier revision")
    assert download(app, "Download final handoff").proto.disabled
    click(app, "Run validation")
    failed = store.list_validations()[-1]["report"]
    assert not failed["passed"]
    assert any(error["code"] == "candidate_revision" for error in failed["errors"])
    click(app, "Rebuild from approved revision")
    click(app, "Run validation")
    assert not download(app, "Download final handoff").proto.disabled
    app.text_area[0].set_value(store.latest_revision()["text"] + "\nA fresh ending.\n")
    click(app, "Save new revision")
    assert store.approval_for(store.latest_revision()["id"]) is None
    assert download(app, "Download final handoff").proto.disabled
    assert not any("Validation passed" in item.value for item in app.tabs[3].success)
    assert any("saved pass belongs to earlier work" in item.value for item in app.tabs[3].warning)
    approve(app)
    assert download(app, "Download final handoff").proto.disabled
    assert not any("Validation passed" in item.value for item in app.tabs[3].success)
    click(app, "Rebuild from approved revision")
    click(app, "Run validation")
    assert not download(app, "Download final handoff").proto.disabled


def test_ui_requires_explicit_decisions_and_preserves_history_on_sample_reload(ui):
    app, store = ui
    click(app, "Load sample manuscript")
    click(app, "Generate suggestions")
    click(app, "Save review decisions")
    assert any("every pending suggestion" in item.value for item in app.error)
    assert len(store.list_revisions()) == 1
    for item in app.radio:
        if item.label.startswith("Suggestion "):
            item.set_value("Reject")
    click(app, "Save review decisions")
    original = store.latest_revision()
    assert original["text"] == SAMPLE_TEXT
    assert [item["decision"] for item in store.list_suggestions(original["id"])] == ["rejected", "rejected"]
    click(app, "Load sample manuscript")
    assert len(store.list_revisions()) == 2
    assert store.latest_revision()["parent_id"] == original["id"]
    assert [item["decision"] for item in store.list_suggestions(original["id"])] == ["rejected", "rejected"]


def test_ui_live_unavailable_is_explicit_and_has_no_recorded_fallback(tmp_path, monkeypatch):
    db_path = tmp_path / "live-unavailable.sqlite3"
    monkeypatch.setenv("MTH_DB_PATH", str(db_path))
    monkeypatch.setenv("MTH_ENABLE_LIVE_AI", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    store = Store(db_path)
    assert not app.exception
    click(app, "Load sample manuscript")
    next(item for item in app.radio if item.label == "Editorial mode").set_value("Live model").run()
    assert not app.exception
    assert any("set OPENAI_API_KEY" in item.value for item in app.warning)
    assert button(app, "Generate suggestions").disabled
    consent = next(item for item in app.checkbox if "send this manuscript to OpenAI" in item.label)
    consent.check().run()
    assert button(app, "Generate suggestions").disabled
    assert store.list_suggestions(store.latest_revision()["id"]) == []


def test_ui_default_sessions_are_isolated_and_resettable(monkeypatch):
    monkeypatch.delenv("MTH_DB_PATH", raising=False)
    monkeypatch.delenv("MTH_ENABLE_LIVE_AI", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    first = AppTest.from_file(str(APP), default_timeout=10).run()
    assert not first.exception
    first_store = first.session_state["_mth_store"]
    assert first_store.latest_revision() is None
    click(first, "Load sample manuscript")
    first_revision = first_store.latest_revision()

    second = AppTest.from_file(str(APP), default_timeout=10).run()
    assert not second.exception
    second_store = second.session_state["_mth_store"]
    assert second_store is not first_store
    assert second_store.latest_revision() is None
    click(second, "Load sample manuscript")
    second_revision = second_store.latest_revision()
    assert second_revision["id"] != first_revision["id"]

    first.session_state["workflow_tab"] = "Edit & review"
    first.run()
    assert first.session_state["workflow_tab"] == "Edit & review"
    click(first, "Generate suggestions")
    assert first_store.latest_revision()["id"] == first_revision["id"]

    click(first, "Reset demo session")
    replacement_store = first.session_state["_mth_store"]
    assert replacement_store is not first_store
    assert replacement_store.latest_revision() is None
    assert first.session_state["workflow_tab"] == "Manuscript"
    assert second_store.latest_revision()["id"] == second_revision["id"]
    click(first, "Load sample manuscript")
    assert replacement_store.latest_revision() is not None


def test_ui_api_key_alone_cannot_enable_live_calls(monkeypatch):
    monkeypatch.delenv("MTH_DB_PATH", raising=False)
    monkeypatch.setenv("MTH_ENABLE_LIVE_AI", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-public-key")

    def unexpected_live_call(*_args, **_kwargs):
        raise AssertionError("Live provider must remain unreachable without explicit enablement")

    monkeypatch.setattr(editorial, "live_review", unexpected_live_call)
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    assert not app.exception
    app.session_state["editorial_mode"] = "Live model"
    click(app, "Load sample manuscript")

    mode = next(item for item in app.radio if item.label == "Editorial mode")
    assert mode.options == ["Recorded demo (authored fixture; no API call)"]
    assert not any("send this manuscript to OpenAI" in item.label for item in app.checkbox)
    click(app, "Generate suggestions")
    store = app.session_state["_mth_store"]
    suggestions = store.list_suggestions(store.latest_revision()["id"])
    assert suggestions
    assert {item["mode"] for item in suggestions} == {"recorded"}
    assert {item["provider_model"] for item in suggestions} == {"authored_fixture"}


def test_ui_enabled_live_mode_requires_fresh_revision_consent(tmp_path, monkeypatch):
    db_path = tmp_path / "live-enabled.sqlite3"
    monkeypatch.setenv("MTH_DB_PATH", str(db_path))
    monkeypatch.setenv("MTH_ENABLE_LIVE_AI", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "mock-editorial-model")
    calls = []

    def recorded_provider(revision):
        calls.append(revision["id"])
        return editorial.recorded_review(revision)

    monkeypatch.setattr(editorial, "live_review", recorded_provider)
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    assert not app.exception
    click(app, "Load sample manuscript")
    next(item for item in app.radio if item.label == "Editorial mode").set_value("Live model").run()
    assert button(app, "Generate suggestions").disabled

    consent = next(item for item in app.checkbox if "send this manuscript to OpenAI" in item.label)
    consent.check().run()
    assert not button(app, "Generate suggestions").disabled
    click(app, "Generate suggestions")
    store = Store(db_path)
    original = store.latest_revision()
    assert calls == [original["id"]]
    suggestions = store.list_suggestions(original["id"])
    assert {item["mode"] for item in suggestions} == {"live"}
    assert {item["provider_model"] for item in suggestions} == {"mock-editorial-model"}

    app.text_area[0].set_value(original["text"] + "\nA new ending.\n")
    click(app, "Save new revision")
    next(item for item in app.radio if item.label == "Editorial mode").set_value("Live model").run()
    fresh_consent = next(item for item in app.checkbox if "send this manuscript to OpenAI" in item.label)
    assert not fresh_consent.value
    assert button(app, "Generate suggestions").disabled
    assert calls == [original["id"]]
