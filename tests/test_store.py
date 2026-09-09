"""Workflow invariants at the persistent storage boundary."""

import hashlib
import sqlite3

import pytest

from mth.store import Store, WorkflowError


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "workspace.sqlite3")


def suggestion(original, replacement):
    return {"original": original, "replacement": replacement, "rationale": "A human may choose this edit."}


def test_exact_unicode_and_line_breaks_persist_across_restart(tmp_path):
    path = tmp_path / "nested" / "workspace.sqlite3"
    original = "  He said, ‘No.’\r\n\r\nCafe\u0301 — café\nاَلْحَمْدُ لِلّٰهِ\n🦋\t \n"
    revision = Store(path).create_revision(original)
    reopened = Store(path)
    assert reopened.get_revision(revision["id"])["text"].encode("utf-8") == original.encode("utf-8")
    assert revision["content_hash"] == hashlib.sha256(original.encode("utf-8")).hexdigest()
    assert reopened.latest_revision() == revision


def test_revisions_form_linear_history_and_cannot_be_changed(store):
    first = store.create_revision("First\n")
    second = store.create_revision("Second\n")
    assert second["parent_id"] == first["id"]
    with pytest.raises(WorkflowError, match="stale"):
        store.create_revision("Branch", parent_id=first["id"])
    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE revisions SET text = 'Changed' WHERE id = ?", (first["id"],))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM revisions WHERE id = ?", (first["id"],))
    assert store.list_revisions() == [first, second]


def test_approval_is_bound_to_exact_revision_even_if_text_is_identical(store):
    first = store.create_revision("Unchanged text")
    approval = store.approve(first["id"], "Author")
    second = store.create_revision("Unchanged text")
    assert first["content_hash"] == second["content_hash"]
    assert store.approval_for(first["id"]) == approval
    assert approval["content_hash"] == first["content_hash"]
    assert store.approval_for(second["id"]) is None
    with pytest.raises(WorkflowError, match="stale"):
        store.approve(first["id"])


def test_accept_and_reject_are_independent_atomic_decisions(store):
    first = store.create_revision("The red bird\nlanded gently.\n")
    store.approve(first["id"])
    rows = store.save_suggestions(
        first["id"], [suggestion("red", "bright blue"), suggestion("gently", "loudly")], "offline"
    )
    second = store.apply_decisions(first["id"], {rows[0]["id"]: "accepted", rows[1]["id"]: "rejected"})
    assert second["text"] == "The bright blue bird\nlanded gently.\n"
    assert second["parent_id"] == first["id"]
    assert second["reason"] == "accepted_suggestions"
    assert store.approval_for(second["id"]) is None
    assert store.get_revision(first["id"])["text"] == "The red bird\nlanded gently.\n"
    recorded = store.list_suggestions(first["id"])
    assert [row["decision"] for row in recorded] == ["accepted", "rejected"]
    assert all(row["result_revision_id"] == second["id"] and row["decided_at"] for row in recorded)


def test_multiple_edits_use_original_positions_and_preserve_untouched_bytes(store):
    original = "⛅ Café\r\n  red and green\r\n\r\nThe end.\t"
    first = store.create_revision(original)
    rows = store.save_suggestions(first["id"], [suggestion("red", "ultramarine"), suggestion("green", "gold")], "offline")
    second = store.apply_decisions(first["id"], {row["id"]: "accepted" for row in reversed(rows)})
    assert second["text"] == "⛅ Café\r\n  ultramarine and gold\r\n\r\nThe end.\t"


def test_all_rejected_creates_no_revision_and_decision_cannot_be_rewritten(store):
    first = store.create_revision("The red bird")
    row = store.save_suggestions(first["id"], [suggestion("red", "blue")], "offline")[0]
    assert store.apply_decisions(first["id"], {row["id"]: "rejected"}) == first
    assert len(store.list_revisions()) == 1
    with pytest.raises(WorkflowError, match="already"):
        store.apply_decisions(first["id"], {row["id"]: "accepted"})
    assert store.list_suggestions(first["id"])[0]["decision"] == "rejected"


@pytest.mark.parametrize("original,passage,status", [("No, no. No, no.", "No, no.", "ambiguous"), ("aaaa", "aa", "ambiguous"), ("There is wind.", "snow", "missing")])
def test_missing_and_repeated_passages_cannot_be_accepted(store, original, passage, status):
    first = store.create_revision(original)
    # Provider offsets cannot disambiguate a repeated phrase.
    proposal = {**suggestion(passage, "Changed"), "start": 0, "end": len(passage)}
    row = store.save_suggestions(first["id"], [proposal], "offline")[0]
    assert row["match_status"] == status
    assert row["start"] is None
    with pytest.raises(WorkflowError, match=status):
        store.apply_decisions(first["id"], {row["id"]: "accepted"})
    assert store.latest_revision() == first
    assert store.list_suggestions(first["id"])[0]["decision"] == "pending"
    assert store.apply_decisions(first["id"], {row["id"]: "rejected"}) == first


def test_overlapping_batch_rolls_back_all_decisions(store):
    first = store.create_revision("The little bird sang.")
    rows = store.save_suggestions(first["id"], [suggestion("little bird", "sparrow"), suggestion("bird sang", "bird cried"), suggestion("The", "A")], "offline")
    with pytest.raises(WorkflowError, match="overlap"):
        store.apply_decisions(first["id"], {rows[0]["id"]: "accepted", rows[1]["id"]: "accepted", rows[2]["id"]: "rejected"})
    assert store.latest_revision() == first
    assert all(row["decision"] == "pending" for row in store.list_suggestions(first["id"]))


def test_duplicate_proposals_cannot_both_be_accepted(store):
    first = store.create_revision("The little bird sang.")
    rows = store.save_suggestions(first["id"], [suggestion("little", "small"), suggestion("little", "tiny")], "offline")
    with pytest.raises(WorkflowError, match="overlap"):
        store.apply_decisions(first["id"], {row["id"]: "accepted" for row in rows})
    result = store.apply_decisions(first["id"], {rows[0]["id"]: "accepted", rows[1]["id"]: "rejected"})
    assert result["text"] == "The small bird sang."


def test_stale_suggestions_and_approvals_do_not_modify_new_revision(store):
    first = store.create_revision("The red bird")
    row = store.save_suggestions(first["id"], [suggestion("red", "blue")], "offline")[0]
    latest = store.create_revision("The yellow bird", first["id"])
    with pytest.raises(WorkflowError, match="stale"):
        store.apply_decisions(first["id"], {row["id"]: "accepted"})
    with pytest.raises(WorkflowError, match="stale"):
        store.save_suggestions(first["id"], [suggestion("red", "blue")], "offline")
    assert store.latest_revision() == latest
    assert store.list_suggestions(first["id"])[0]["decision"] == "pending"


def test_wrong_revision_suggestion_and_unknown_id_roll_back_batch(store):
    first = store.create_revision("The red bird")
    old = store.save_suggestions(first["id"], [suggestion("red", "blue")], "offline")[0]
    latest = store.create_revision("The yellow bird")
    new = store.save_suggestions(latest["id"], [suggestion("yellow", "green")], "offline")[0]
    for invalid_id in (old["id"], "not-a-real-id"):
        with pytest.raises(WorkflowError, match="does not belong"):
            store.apply_decisions(latest["id"], {new["id"]: "rejected", invalid_id: "accepted"})
    assert store.list_suggestions(latest["id"])[0]["decision"] == "pending"
    assert store.latest_revision() == latest


def test_suggestion_save_is_atomic_if_any_proposal_is_invalid(store):
    first = store.create_revision("The red bird")
    with pytest.raises(WorkflowError, match="original passage"):
        store.save_suggestions(first["id"], [suggestion("red", "blue"), suggestion("", "extra")], "offline")
    assert store.list_suggestions(first["id"]) == []


def test_candidates_and_validation_history_survive_restart(tmp_path):
    path = tmp_path / "store.db"
    store = Store(path)
    first = {"id": "candidate-one", "revision_id": "rev-example", "text": "A\r\nB", "spreads": [{"page": 1}]}
    assert store.save_candidate(first) == "candidate-one"
    validation_id = store.save_validation("candidate-one", {"status": "failed", "issues": ["Overflow"]})
    store.save_validation("candidate-one", {"status": "passed", "issues": []})
    second = {"id": "candidate-two", "text": "新しい\n"}
    store.save_candidate(second)
    store.save_validation("candidate-two", {"status": "failed"})
    reopened = Store(path)
    assert reopened.list_candidates() == [first, second]
    assert reopened.latest_candidate() == second
    reports = reopened.list_validations("candidate-one")
    assert reports[0]["id"] == validation_id
    assert [row["report"]["status"] for row in reports] == ["failed", "passed"]
    assert len(reopened.list_validations()) == 3
    with pytest.raises(WorkflowError, match="cannot be replaced"):
        reopened.save_candidate({"id": "candidate-one", "text": "Edited"})
    with pytest.raises(WorkflowError, match="does not exist"):
        reopened.save_validation("missing", {"status": "passed"})


def test_empty_store_and_in_memory_workspace():
    store = Store(":memory:")
    assert store.latest_revision() is None
    assert store.latest_candidate() is None
    assert store.list_revisions() == []
    assert store.list_validations() == []
    revision = store.create_revision("")
    assert store.latest_revision() == revision
