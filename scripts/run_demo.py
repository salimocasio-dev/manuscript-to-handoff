"""Reproduce the workflow with fictional data and scripted TEST approvals.

This is executed evidence, not a replacement for exercising the interface.
Run from the repository root: python scripts/run_demo.py --output artifacts/demo
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mth.editorial import SAMPLE_TEXT, recorded_review
from mth.handoff import ExportBlocked, build_candidate, corrupt_candidate, export_package
from mth.store import Store
from mth.validation import validate_candidate, validate_package


def write_json(path: Path, value) -> None:
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def run(output: Path) -> dict:
    if output.exists():
        raise ValueError(f"Output already exists: {output}. Choose a new --output directory to preserve previous evidence.")
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="mth-evidence-") as temporary:
        db = Path(temporary) / "workflow.sqlite3"
        store = Store(db)
        original = store.create_revision(SAMPLE_TEXT, reason="fictional_demo_sample")
        suggestions = store.save_suggestions(original["id"], recorded_review(original),
                                             "recorded-example", "authored fixture; no API call")
        revised = store.apply_decisions(original["id"], {
            suggestions[0]["id"]: "accepted", suggestions[1]["id"]: "rejected"})
        assert store.get_revision(original["id"])["text"] == SAMPLE_TEXT
        assert revised["id"] != original["id"]
        assert store.approval_for(revised["id"]) is None
        reload_store = Store(db)
        assert [s["decision"] for s in reload_store.list_suggestions(original["id"])] == ["accepted", "rejected"]

        # Scripted test action only: this does not claim Salim approved a manuscript.
        approval = store.approve(revised["id"], actor="Scripted test reviewer (fictional demo only)")
        clean = build_candidate(revised, approval)
        store.save_candidate(clean)
        clean_report = validate_candidate(clean, revised, approval)
        store.save_validation(clean["id"], clean_report)
        assert clean_report["passed"]
        package = export_package(clean, revised, approval)
        package_report = validate_package(package, revised, approval, clean)
        assert package_report["passed"]
        (output / "clean_handoff.zip").write_bytes(package)
        write_json(output / "clean_candidate.json", clean)
        write_json(output / "clean_validation.json", clean_report)
        write_json(output / "package_validation.json", package_report)

        failures = {}
        for kind in ("missing_line", "duplicated", "altered", "reordered", "wrong_revision"):
            broken = corrupt_candidate(clean, kind, wrong_revision=original)
            store.save_candidate(broken)
            report = validate_candidate(broken, revised, approval)
            store.save_validation(broken["id"], report)
            assert not report["passed"], kind
            try:
                export_package(broken, revised, approval)
            except ExportBlocked:
                export_blocked = True
            else:
                raise AssertionError(f"Corruption escaped export gate: {kind}")
            failures[kind] = {"rejected": True, "export_blocked": export_blocked,
                              "errors": sorted({error["code"] for error in report["errors"]})}
            write_json(output / f"{kind}_candidate.json", broken)
            write_json(output / f"{kind}_validation.json", report)

        recovery = build_candidate(revised, approval)
        store.save_candidate(recovery)
        recovery_report = validate_candidate(recovery, revised, approval)
        store.save_validation(recovery["id"], recovery_report)
        recovered_bytes = export_package(recovery, revised, approval)
        assert validate_package(recovered_bytes, revised, approval, recovery)["passed"]
        (output / "recovered_handoff.zip").write_bytes(recovered_bytes)
        write_json(output / "recovery_validation.json", recovery_report)
        assert store.get_revision(revised["id"])["text"] == revised["text"]

        next_revision = store.create_revision(revised["text"] + "\n", reason="test_new_draft")
        assert store.approval_for(next_revision["id"]) is None
        try:
            export_package(build_candidate(next_revision, None), next_revision, None)
        except ExportBlocked:
            new_draft_blocked = True
        else:
            raise AssertionError("New draft exported without fresh approval")
        write_json(output / "intended_revision.json", revised)
        write_json(output / "approval.json", approval)
        write_json(output / "decisions.json", store.list_suggestions(original["id"]))
        (output / "approved_manuscript.txt").write_bytes(revised["text"].encode("utf-8"))
        evidence = {
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "source": "fictional purpose-written sample",
            "editorial_mode": "authored recorded fixture; no API call",
            "approval_actor_is_test_fixture": True,
            "original_preserved": True, "accepted_and_rejected_decisions_survived_reload": True,
            "clean_candidate_passed": clean_report["passed"],
            "actual_zip_passed": package_report["passed"],
            "clean_zip_sha256": hashlib.sha256(package).hexdigest(),
            "corruptions": failures,
            "recovery_passed": recovery_report["passed"],
            "new_draft_without_approval_blocked": new_draft_blocked,
            "persisted_validation_count": len(Store(db).list_validations()),
            "live_api": "UNVERIFIED: no live request made",
            "scope": "Text integrity and workflow controls only; no editorial quality or print-readiness certification.",
        }
        write_json(output / "evidence.json", evidence)
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/demo"))
    args = parser.parse_args()
    try:
        evidence = run(args.output)
    except ValueError as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps(evidence, indent=2))
