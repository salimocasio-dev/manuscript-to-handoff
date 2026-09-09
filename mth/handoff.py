"""Candidate generation and the final, independently checked export boundary."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import uuid
import zipfile

from mth.validation import validate_candidate, validate_package


class ExportBlocked(ValueError):
    """Raised with the actual failure report whenever final export is blocked."""

    def __init__(self, report: dict):
        self.report = report
        super().__init__("Production export blocked: " + "; ".join(
            error["message"] for error in report["errors"][:3]))


def build_candidate(revision: dict, approval: dict | None, spread_count: int = 4) -> dict:
    """Allocate unchanged source lines to a small, fixed number of spreads.

    This is controlled text transfer, not editorial pagination. Generation does
    not decide whether the candidate is approved or valid; the validator does.
    """
    if type(spread_count) is not int or not 1 <= spread_count <= 32:
        raise ValueError("spread_count must be an integer from 1 to 32")
    source = revision["text"]
    if not isinstance(source, str):
        raise ValueError("Revision text must be a string")
    lines = source.splitlines(keepends=True)
    spreads = [{"number": number, "spans": []} for number in range(1, spread_count + 1)]
    offset = 0
    for index, line in enumerate(lines):
        target = min(index * spread_count // max(len(lines), 1), spread_count - 1)
        spreads[target]["spans"].append({"start": offset, "end": offset + len(line), "text": line})
        offset += len(line)
    return {
        "id": "handoff_" + uuid.uuid4().hex,
        "revision_id": revision["id"],
        "content_hash": revision["content_hash"],
        "manuscript": source,
        "spreads": spreads,
        "production_brief": (
            "# Production handoff\n\n"
            f"Source revision: {revision['id']}\n\n"
            f"Allocate the approved source text across {spread_count} ordered spreads as supplied in spreads.json. "
            "Span start/end values are half-open Python Unicode code-point offsets into manuscript.txt. "
            "Concatenate span text in spread order to recover the manuscript exactly, including line endings.\n\n"
            "Preserve all wording, punctuation, and line breaks. Keep art direction and production notes "
            "outside source spans. Any editorial change requires a new revision and separate human approval.\n\n"
            "This fixed allocation demonstrates controlled text transfer. An editor and designer still need "
            "to decide pacing, pagination, illustration, typography, and print specifications. "
            "Text-integrity validation does not certify editorial quality or print readiness.\n"
        ),
        "issues": [{"severity": "warning", "message":
                    "Spread allocation is a workflow demonstration; professional pagination and design remain outstanding."}],
        "approval": deepcopy(approval),
    }


def corrupt_candidate(candidate: dict, kind: str, wrong_revision: dict | None = None) -> dict:
    """Return a genuinely corrupted copy; never change the approved source."""
    result = deepcopy(candidate)
    result["id"] = "handoff_" + uuid.uuid4().hex
    if kind == "wrong_revision":
        if wrong_revision is not None:
            old = build_candidate(wrong_revision, candidate.get("approval"), len(candidate["spreads"]))
            old["id"] = result["id"]
            return old
        result["revision_id"] = "wrong_revision_" + candidate["revision_id"]
        return result
    positions = [(spread, index) for spread in result["spreads"]
                 for index in range(len(spread["spans"]))]
    if not positions:
        raise ValueError("This demo needs a nonempty manuscript")
    if kind == "missing_line":
        spread, index = positions[0]
        del spread["spans"][index]
    elif kind == "duplicated":
        spread, index = positions[0]
        spread["spans"].insert(index + 1, deepcopy(spread["spans"][index]))
    elif kind == "altered":
        spread, index = positions[0]
        span = spread["spans"][index]
        span["text"] = "X" + span["text"][1:] if span["text"][:1] != "X" else "Y" + span["text"][1:]
    elif kind == "reordered":
        if len(positions) < 2:
            raise ValueError("Reordering requires at least two source lines")
        first, i = positions[0]
        second, j = positions[1]
        first["spans"][i], second["spans"][j] = second["spans"][j], first["spans"][i]
    else:
        raise ValueError(f"Unknown demo corruption: {kind}")
    return result


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def export_package(candidate: dict, intended_revision: dict, approval: dict | None) -> bytes:
    """Freshly validate, package, inspect actual ZIP bytes, then allow download."""
    # Snapshot inputs so the validated object and serialized object cannot drift.
    candidate, intended_revision, approval = deepcopy((candidate, intended_revision, approval))
    report = validate_candidate(candidate, intended_revision, approval)
    if not report["passed"]:
        raise ExportBlocked(report)
    files = {
        "manuscript.txt": candidate["manuscript"].encode("utf-8"),
        "spreads.json": _json_bytes(candidate["spreads"]),
        "production_brief.md": candidate["production_brief"].encode("utf-8"),
        "issues.json": _json_bytes(candidate["issues"]),
        "approval.json": _json_bytes(candidate["approval"]),
        "candidate.json": _json_bytes(candidate),
        "validation_report.json": _json_bytes(report),
    }
    files["manifest.json"] = _json_bytes({
        "schema_version": 1,
        "candidate_hash": report["candidate_hash"],
        "source_revision_id": intended_revision["id"],
        "source_content_hash": intended_revision["content_hash"],
        "files": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()},
    })
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, data)
    package = output.getvalue()
    package_report = validate_package(package, intended_revision, approval, candidate)
    if not package_report["passed"]:
        raise ExportBlocked(package_report)
    return package
