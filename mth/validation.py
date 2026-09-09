"""Independent, deterministic checks of actual handoff text and ZIP contents.

The trusted inputs are the intended revision and approval read independently
from the workflow store. A candidate or manifest cannot supply its own trust.
Offsets are half-open Python Unicode code-point indices; nothing is normalized.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any


PACKAGE_FILES = frozenset({
    "manuscript.txt", "spreads.json", "production_brief.md", "issues.json",
    "approval.json", "candidate.json", "validation_report.json", "manifest.json",
})
MAX_PACKAGE_BYTES = 10 * 1024 * 1024


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def candidate_hash(candidate: dict) -> str:
    return hashlib.sha256(canonical_bytes(candidate)).hexdigest()


def _report() -> dict:
    return {"passed": False, "checks": [], "errors": [], "warnings": [],
            "candidate_hash": None}


def _check(report: dict, code: str, passed: bool, message: str, **location: Any) -> None:
    report["checks"].append({"code": code, "passed": bool(passed), "message": message})
    if not passed:
        report["errors"].append({"code": code, "message": message, **location})


def _finish(report: dict) -> dict:
    report["passed"] = not report["errors"]
    return report


def _first_difference(expected: str, actual: str) -> int:
    return next((i for i, (a, b) in enumerate(zip(expected, actual)) if a != b),
                min(len(expected), len(actual)))


def validate_candidate(candidate: dict, intended_revision: dict,
                       approval: dict | None) -> dict:
    """Validate body text, approval binding, and complete ordered source coverage."""
    report = _report()
    if not isinstance(candidate, dict) or not isinstance(intended_revision, dict):
        _check(report, "candidate_schema", False, "Candidate and source revision must be objects.")
        return _finish(report)
    try:
        report["candidate_hash"] = candidate_hash(candidate)
    except (TypeError, ValueError, UnicodeError):
        _check(report, "candidate_schema", False, "Candidate must contain valid UTF-8 JSON data.")
        return _finish(report)

    source = intended_revision.get("text")
    if not isinstance(source, str):
        _check(report, "source_text", False, "The independent source revision has no text.")
        return _finish(report)
    source_digest = text_hash(source)
    revision_id = intended_revision.get("id")
    _check(report, "source_hash", intended_revision.get("content_hash") == source_digest,
           "The stored source hash must match the independently read source text.")
    _check(report, "source_revision_id", isinstance(revision_id, str) and bool(revision_id),
           "The intended source revision must have a nonempty revision ID.")
    _check(report, "candidate_revision", candidate.get("revision_id") == revision_id,
           "The candidate must use the intended revision.",
           expected_revision_id=revision_id, actual_revision_id=candidate.get("revision_id"))
    _check(report, "candidate_source_hash", candidate.get("content_hash") == source_digest,
           "The candidate's source hash must match the actual intended manuscript.")
    _check(report, "candidate_id", isinstance(candidate.get("id"), str) and bool(candidate.get("id")),
           "The candidate must have a nonempty ID.")

    has_approval = isinstance(approval, dict) and all(
        isinstance(approval.get(key), str) and bool(approval[key].strip())
        for key in ("id", "revision_id", "content_hash", "actor", "approved_at")
    )
    _check(report, "approval_required", has_approval,
           "A separate human approval record with actor and timestamp is required.")
    _check(report, "approval_binding", bool(has_approval and
           approval.get("revision_id") == revision_id and approval.get("content_hash") == source_digest),
           "The independent approval must bind this exact source revision and hash.")
    _check(report, "approval_record", bool(has_approval and candidate.get("approval") == approval),
           "The candidate must carry the complete independent approval record unchanged.")

    manuscript = candidate.get("manuscript")
    matches = isinstance(manuscript, str) and manuscript == source
    difference = _first_difference(source, manuscript) if isinstance(manuscript, str) else 0
    _check(report, "manuscript_exact", matches,
           "Candidate manuscript wording, punctuation, and line breaks must exactly match approval.",
           source_offset=difference, source_line=source[:difference].count("\n") + 1,
           expected_excerpt=source[difference:difference + 80],
           actual_excerpt=manuscript[difference:difference + 80] if isinstance(manuscript, str) else None)

    spreads = candidate.get("spreads")
    spreads_valid = isinstance(spreads, list) and len(spreads) > 0
    _check(report, "spreads_required", spreads_valid, "An ordered spread allocation is required.")
    cursor = 0
    reconstructed: list[str] = []
    if spreads_valid:
        for spread_index, spread in enumerate(spreads, start=1):
            if not isinstance(spread, dict):
                _check(report, "spread_schema", False, "Each spread must be an object.", spread=spread_index)
                continue
            _check(report, "spread_order", type(spread.get("number")) is int and
                   spread.get("number") == spread_index,
                   f"Spread {spread_index} must occupy its declared position.", spread=spread_index)
            spans = spread.get("spans")
            if not isinstance(spans, list):
                _check(report, "span_schema", False, "Each spread must provide a spans list.", spread=spread_index)
                continue
            for span_index, span in enumerate(spans, start=1):
                loc = {"spread": spread_index, "span": span_index}
                valid = isinstance(span, dict) and type(span.get("start")) is int and \
                    type(span.get("end")) is int and isinstance(span.get("text"), str)
                if not valid:
                    _check(report, "span_schema", False, "A span needs integer start/end and exact text.", **loc)
                    continue
                start, end, value = span["start"], span["end"], span["text"]
                loc.update(source_offset=start, source_line=source[:max(0, start)].count("\n") + 1)
                bounds = 0 <= start < end <= len(source)
                _check(report, "span_bounds", bounds,
                       f"Spread {spread_index}, span {span_index}: source range [{start}, {end}) must be valid and nonempty.", **loc)
                _check(report, "span_coverage", start == cursor,
                       f"Spread {spread_index}, span {span_index}: expected source offset {cursor}, received {start}; gaps, duplicates, and reordering are blocked.",
                       expected_start=cursor, actual_start=start, **loc)
                _check(report, "span_text", bounds and value == source[start:end],
                       f"Spread {spread_index}, span {span_index}: allocated text must equal its exact source slice.", **loc)
                reconstructed.append(value)
                cursor = end
        _check(report, "span_complete", cursor == len(source),
               f"Ordered spans must end at source offset {len(source)}; received {cursor}.",
               source_offset=cursor, expected_end=len(source), actual_end=cursor)
        joined = "".join(reconstructed)
        difference = _first_difference(source, joined)
        _check(report, "reconstructed_exact", joined == source,
               "Concatenating actual ordered spread text must reproduce the approved manuscript exactly.",
               source_offset=difference, source_line=source[:difference].count("\n") + 1,
               expected_excerpt=source[difference:difference + 80], actual_excerpt=joined[difference:difference + 80])

    _check(report, "production_brief", isinstance(candidate.get("production_brief"), str) and
           bool(candidate["production_brief"].strip()), "A separate production brief is required.")
    issues = candidate.get("issues")
    valid_issues = isinstance(issues, list) and all(
        isinstance(issue, dict) and issue.get("severity") in ("warning", "blocking") and
        isinstance(issue.get("message"), str) and bool(issue["message"].strip()) for issue in issues
    )
    _check(report, "issues_schema", valid_issues, "Issues must identify warning or blocking severity and a message.")
    if valid_issues:
        for index, issue in enumerate(issues):
            if issue["severity"] == "blocking":
                _check(report, "blocking_issue", False, issue["message"], issue_index=index)
            else:
                report["warnings"].append({"code": "production_warning", "message": issue["message"], "issue_index": index})
    return _finish(report)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_package(package_bytes: bytes, intended_revision: dict,
                     approval: dict | None, expected_candidate: dict | None = None) -> dict:
    """Read actual ZIP bytes, revalidate against source, and verify each file.

    A recomputed manifest cannot bless an altered manuscript. No extraction is
    performed. Limits bound memory use for untrusted or accidentally huge ZIPs.
    """
    report = _report()
    if not isinstance(package_bytes, bytes) or len(package_bytes) > MAX_PACKAGE_BYTES:
        _check(report, "package_size", False, "Package must be bytes and at most 10 MiB.")
        return _finish(report)
    report["package_hash"] = hashlib.sha256(package_bytes).hexdigest()
    try:
        with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            _check(report, "zip_unique_names", len(names) == len(set(names)),
                   "ZIP member names must be unique; duplicate entries are blocked.")
            _check(report, "package_files", set(names) == PACKAGE_FILES,
                   "The package must contain exactly the required handoff files.",
                   missing=sorted(PACKAGE_FILES - set(names)), unexpected=sorted(set(names) - PACKAGE_FILES))
            _check(report, "package_expanded_size", sum(entry.file_size for entry in entries) <= MAX_PACKAGE_BYTES,
                   "The expanded package must be at most 10 MiB.")
            if report["errors"]:
                return _finish(report)
            payload = {name: archive.read(name) for name in names}
        decoded = {name: value.decode("utf-8") for name, value in payload.items()}
        objects = {name: json.loads(value, object_pairs_hook=_unique_json_object)
                   for name, value in decoded.items() if name.endswith(".json")}
    except (zipfile.BadZipFile, UnicodeError, ValueError, RuntimeError, OSError, NotImplementedError) as exc:
        _check(report, "package_readable", False, f"Cannot decode a valid UTF-8 handoff ZIP: {exc}")
        return _finish(report)

    candidate = objects["candidate.json"]
    if not isinstance(candidate, dict):
        _check(report, "candidate_schema", False, "candidate.json must be an object.")
        return _finish(report)
    actual = dict(candidate)
    actual.update(manuscript=decoded["manuscript.txt"], spreads=objects["spreads.json"],
                  production_brief=decoded["production_brief.md"], issues=objects["issues.json"],
                  approval=objects["approval.json"])
    _check(report, "artifact_consistency", actual == candidate,
           "Actual manuscript, spread, brief, issue, and approval files must match candidate.json.")
    if expected_candidate is not None:
        _check(report, "validated_candidate_match", actual == expected_candidate,
               "The packaged artifact must match the candidate passed to the export boundary.")

    actual_report = validate_candidate(actual, intended_revision, approval)
    report["candidate_hash"] = actual_report["candidate_hash"]
    report["checks"].extend(actual_report["checks"])
    report["errors"].extend(actual_report["errors"])
    report["warnings"].extend(actual_report["warnings"])
    _check(report, "embedded_validation_report", objects["validation_report.json"] == actual_report,
           "The saved candidate validation report must equal a fresh validation of the actual artifact files.")

    manifest = objects["manifest.json"]
    manifest_valid = isinstance(manifest, dict) and isinstance(manifest.get("files"), dict)
    _check(report, "manifest_schema", manifest_valid, "The file-hash manifest must provide a files mapping.")
    if manifest_valid:
        expected_names = PACKAGE_FILES - {"manifest.json"}
        _check(report, "manifest_files", set(manifest["files"]) == expected_names,
               "The manifest must hash every other package file, without a self hash.")
        for name in sorted(expected_names):
            _check(report, "file_hash", manifest["files"].get(name) == hashlib.sha256(payload[name]).hexdigest(),
                   f"{name}: manifest SHA-256 must match the actual file bytes.", file=name)
        _check(report, "manifest_candidate", manifest.get("candidate_hash") == actual_report["candidate_hash"],
               "The manifest candidate hash must match actual packaged artifact contents.")
        _check(report, "manifest_source", manifest.get("source_revision_id") == intended_revision.get("id") and
               manifest.get("source_content_hash") == text_hash(intended_revision.get("text", "")),
               "Manifest source metadata must match the independent intended source.")
        _check(report, "manifest_version", manifest.get("schema_version") == 1,
               "The manifest must use the supported schema version.")
    return _finish(report)
