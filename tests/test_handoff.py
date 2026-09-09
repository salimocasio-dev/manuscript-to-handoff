"""Evidence that controls inspect real contents rather than self-reported hashes."""

from copy import deepcopy
import hashlib
import io
import json
import zipfile

import pytest

from mth.handoff import ExportBlocked, build_candidate, corrupt_candidate, export_package
from mth.validation import PACKAGE_FILES, candidate_hash, text_hash, validate_candidate, validate_package


@pytest.fixture
def source():
    # Non-ASCII, combining code points, blank lines, CRLF, and no final newline.
    text = "The paper moon.\r\n\r\n‘Come back,’ said Nia.\nCafe\u0301 lights glowed.\n🌙 She waited."
    return {"id": "revision_approved", "text": text, "content_hash": text_hash(text)}


@pytest.fixture
def approval(source):
    return {"id": "approval_1", "revision_id": source["id"], "content_hash": source["content_hash"],
            "actor": "Demo reviewer", "approved_at": "2026-09-09T12:00:00+00:00"}


def _errors(report):
    return {error["code"] for error in report["errors"]}


def _payload(package):
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return buffer.getvalue()


def _recompute_manifest(files):
    manifest = json.loads(files["manifest.json"])
    manifest["files"] = {name: hashlib.sha256(value).hexdigest()
                         for name, value in files.items() if name != "manifest.json"}
    manifest["candidate_hash"] = candidate_hash(json.loads(files["candidate.json"]))
    files["manifest.json"] = json.dumps(manifest).encode("utf-8")


def test_clean_candidate_preserves_every_codepoint_and_line_ending(source, approval):
    original = deepcopy(source)
    candidate = build_candidate(source, approval)
    assert candidate["manuscript"] == source["text"]
    spans = [span for spread in candidate["spreads"] for span in spread["spans"]]
    assert "".join(span["text"] for span in spans) == source["text"]
    assert [span["text"] for span in spans] == source["text"].splitlines(keepends=True)
    assert all(span["text"] == source["text"][span["start"]:span["end"]] for span in spans)
    report = validate_candidate(candidate, source, approval)
    assert report["passed"], report
    assert report["warnings"]
    assert source == original


@pytest.mark.parametrize("kind,expected", [
    ("missing_line", "span_coverage"), ("duplicated", "span_coverage"),
    ("altered", "span_text"), ("reordered", "span_coverage"),
    ("wrong_revision", "candidate_revision"),
])
def test_genuine_corruptions_fail_and_block_export(source, approval, kind, expected):
    clean = build_candidate(source, approval)
    before = deepcopy(clean)
    corrupt = corrupt_candidate(clean, kind)
    assert corrupt != clean
    assert corrupt["id"] != clean["id"]
    assert clean == before
    assert source["content_hash"] == text_hash(source["text"])
    report = validate_candidate(corrupt, source, approval)
    assert not report["passed"]
    assert expected in _errors(report)
    with pytest.raises(ExportBlocked) as caught:
        export_package(corrupt, source, approval)
    assert caught.value.report == report
    # Recovery is a fresh generation, revalidation, and actual ZIP inspection.
    corrected = build_candidate(source, approval)
    assert validate_package(export_package(corrected, source, approval), source, approval)["passed"]


def test_wrong_revision_contains_actual_old_manuscript(source, approval):
    old_text = "The moon was a yellow button.\nIt fell.\n"
    old = {"id": "revision_original", "text": old_text, "content_hash": text_hash(old_text)}
    corrupt = corrupt_candidate(build_candidate(source, approval), "wrong_revision", old)
    assert corrupt["manuscript"] == old_text
    assert "".join(span["text"] for spread in corrupt["spreads"] for span in spread["spans"]) == old_text
    report = validate_candidate(corrupt, source, approval)
    assert {"candidate_revision", "candidate_source_hash", "manuscript_exact"} <= _errors(report)


@pytest.mark.parametrize("trusted_approval", [None, {}, {"actor": "AI"}])
def test_candidate_cannot_supply_its_own_approval(source, approval, trusted_approval):
    candidate = build_candidate(source, approval)
    with pytest.raises(ExportBlocked) as caught:
        export_package(candidate, source, trusted_approval)
    assert "approval_required" in _errors(caught.value.report)


def test_old_approval_cannot_approve_a_new_revision_even_with_same_text(source, approval):
    new_revision = {**source, "id": "revision_new"}
    candidate = build_candidate(new_revision, approval)
    report = validate_candidate(candidate, new_revision, approval)
    assert "approval_binding" in _errors(report)


@pytest.mark.parametrize("field,value", [("actor", "Someone else"), ("id", "forged_id"),
                                         ("approved_at", "2099-01-01T00:00:00Z")])
def test_complete_approval_record_must_match_independent_record(source, approval, field, value):
    candidate = build_candidate(source, approval)
    candidate["approval"][field] = value
    assert "approval_record" in _errors(validate_candidate(candidate, source, approval))


def test_source_hash_is_recomputed_and_never_trusted(source, approval):
    altered_source = {**source, "text": source["text"] + "!"}
    candidate = build_candidate(altered_source, approval)
    report = validate_candidate(candidate, altered_source, approval)
    assert {"source_hash", "candidate_source_hash", "approval_binding"} <= _errors(report)


def test_real_zip_bytes_have_all_files_hashes_and_independent_validation(source, approval):
    candidate = build_candidate(source, approval)
    package = export_package(candidate, source, approval)
    files = _payload(package)
    assert set(files) == PACKAGE_FILES
    assert files["manuscript.txt"] == source["text"].encode("utf-8")
    manifest = json.loads(files["manifest.json"])
    assert set(manifest["files"]) == PACKAGE_FILES - {"manifest.json"}
    assert all(manifest["files"][name] == hashlib.sha256(files[name]).hexdigest()
               for name in manifest["files"])
    assert validate_package(package, source, approval, candidate)["passed"]
    # Identical input snapshot yields identical package bytes.
    assert export_package(candidate, source, approval) == package


def test_actual_manuscript_tamper_rejected_even_after_manifest_recomputed(source, approval):
    files = _payload(export_package(build_candidate(source, approval), source, approval))
    files["manuscript.txt"] = files["manuscript.txt"].replace(b"paper", b"silver")
    _recompute_manifest(files)
    report = validate_package(_zip(files), source, approval)
    assert {"artifact_consistency", "manuscript_exact"} <= _errors(report)
    assert "file_hash" not in _errors(report)  # Matching hashes do not prove integrity.


def test_consistently_forged_candidate_report_and_manifest_cannot_bless_missing_text(source, approval):
    clean = build_candidate(source, approval)
    files = _payload(export_package(clean, source, approval))
    forged = corrupt_candidate(clean, "missing_line")
    forged["manuscript"] = "".join(span["text"] for spread in forged["spreads"] for span in spread["spans"])
    files["candidate.json"] = json.dumps(forged).encode("utf-8")
    files["manuscript.txt"] = forged["manuscript"].encode("utf-8")
    files["spreads.json"] = json.dumps(forged["spreads"]).encode("utf-8")
    # Even a report generated from the tampered body and a perfectly consistent
    # manifest cannot cause approval of the actual missing manuscript line.
    files["validation_report.json"] = json.dumps(validate_candidate(forged, source, approval)).encode("utf-8")
    _recompute_manifest(files)
    report = validate_package(_zip(files), source, approval)
    assert {"manuscript_exact", "span_coverage", "reconstructed_exact"} <= _errors(report)
    assert "artifact_consistency" not in _errors(report)
    assert "file_hash" not in _errors(report)


def test_packaged_output_must_match_specific_validated_candidate(source, approval):
    candidate = build_candidate(source, approval)
    changed = deepcopy(candidate)
    changed["production_brief"] += "\nExtra instruction.\n"
    assert validate_candidate(changed, source, approval)["passed"]
    package = export_package(changed, source, approval)
    assert "validated_candidate_match" in _errors(validate_package(package, source, approval, candidate))


def test_tampered_success_report_is_rejected(source, approval):
    files = _payload(export_package(build_candidate(source, approval), source, approval))
    files["validation_report.json"] = b'{"passed": true}'
    _recompute_manifest(files)
    assert "embedded_validation_report" in _errors(validate_package(_zip(files), source, approval))


def test_duplicate_zip_filenames_are_blocked(source, approval):
    package = export_package(build_candidate(source, approval), source, approval)
    output = io.BytesIO(package)
    with pytest.warns(UserWarning), zipfile.ZipFile(output, "a") as archive:
        archive.writestr("manuscript.txt", "different source")
    assert "zip_unique_names" in _errors(validate_package(output.getvalue(), source, approval))


def test_missing_package_file_is_blocked(source, approval):
    files = _payload(export_package(build_candidate(source, approval), source, approval))
    del files["approval.json"]
    assert "package_files" in _errors(validate_package(_zip(files), source, approval))


@pytest.mark.parametrize("kind", ["negative", "boolean", "overrun", "empty"])
def test_invalid_span_boundaries_are_blocked(source, approval, kind):
    candidate = build_candidate(source, approval)
    span = candidate["spreads"][0]["spans"][0]
    if kind == "negative":
        span["start"] = -1
    elif kind == "boolean":
        span["start"] = False
    elif kind == "overrun":
        span["end"] = len(source["text"]) + 1
    else:
        span["end"] = span["start"]
    assert not validate_candidate(candidate, source, approval)["passed"]


def test_blocking_issues_prevent_export_but_warnings_do_not(source, approval):
    candidate = build_candidate(source, approval)
    assert validate_candidate(candidate, source, approval)["passed"]
    candidate["issues"].append({"severity": "blocking", "message": "Unresolved production instruction."})
    with pytest.raises(ExportBlocked) as caught:
        export_package(candidate, source, approval)
    assert "blocking_issue" in _errors(caught.value.report)


def test_whitespace_normalization_is_not_accepted(source, approval):
    candidate = build_candidate(source, approval)
    candidate["manuscript"] = candidate["manuscript"].replace("\r\n", "\n")
    assert "manuscript_exact" in _errors(validate_candidate(candidate, source, approval))
