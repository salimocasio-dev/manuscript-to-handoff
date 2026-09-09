"""Durable, local manuscript workflow with revision-bound human decisions.

Manuscript text is never normalized. A revision's hash is the SHA-256 digest of
its exact UTF-8 bytes, including whitespace, Unicode composition, and newlines.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
import uuid


class WorkflowError(ValueError):
    """An action would violate the manuscript review workflow."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _id() -> str:
    return str(uuid.uuid4())


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Store:
    """One linear workspace backed by a SQLite file.

    Each public mutation uses a single write transaction. Old revisions,
    approvals, candidates, and validation reports are append-only. Suggestions
    retain their decision and the resulting revision for an audit trail.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        # Keep an in-memory connection alive when requested by callers/tests.
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = self._connect()
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS revisions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    text TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    parent_id TEXT REFERENCES revisions(id),
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    revision_id TEXT NOT NULL REFERENCES revisions(id),
                    content_hash TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    approved_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS suggestions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    revision_id TEXT NOT NULL REFERENCES revisions(id),
                    original TEXT NOT NULL,
                    replacement TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    start INTEGER,
                    end INTEGER,
                    match_status TEXT NOT NULL,
                    decision TEXT NOT NULL DEFAULT 'pending'
                        CHECK(decision IN ('pending', 'accepted', 'rejected')),
                    mode TEXT NOT NULL,
                    provider_model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    result_revision_id TEXT REFERENCES revisions(id)
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS validations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    candidate_id TEXT NOT NULL REFERENCES candidates(id),
                    report TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS suggestions_revision
                    ON suggestions(revision_id, sequence);
                CREATE INDEX IF NOT EXISTS approvals_revision
                    ON approvals(revision_id, sequence);
                CREATE INDEX IF NOT EXISTS validations_candidate
                    ON validations(candidate_id, sequence);
                """
            )
            # These protect immutability even against accidental future SQL.
            for table in ("revisions", "approvals", "candidates", "validations"):
                for operation in ("UPDATE", "DELETE"):
                    connection.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} "
                        f"BEFORE {operation} ON {table} BEGIN "
                        f"SELECT RAISE(ABORT, '{table} are immutable'); END"
                    )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._memory_connection or self._connect()
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except Exception:
            if write:
                connection.rollback()
            raise
        finally:
            if connection is not self._memory_connection:
                connection.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result.pop("sequence", None)
        return result

    def _revision(self, connection: sqlite3.Connection, revision_id: str) -> dict[str, Any]:
        row = self._row(connection.execute("SELECT * FROM revisions WHERE id = ?", (revision_id,)).fetchone())
        if row is None:
            raise WorkflowError("Revision does not exist.")
        return row

    def _latest(self, connection: sqlite3.Connection) -> dict[str, Any] | None:
        return self._row(connection.execute("SELECT * FROM revisions ORDER BY sequence DESC LIMIT 1").fetchone())

    def _require_latest(self, connection: sqlite3.Connection, revision_id: str) -> dict[str, Any]:
        revision = self._revision(connection, revision_id)
        latest = self._latest(connection)
        if latest is None or latest["id"] != revision_id:
            raise WorkflowError("This revision is stale. Review the latest manuscript revision before continuing.")
        return revision

    def _insert_revision(
        self, connection: sqlite3.Connection, text: str, parent_id: str | None, reason: str
    ) -> dict[str, Any]:
        revision = {
            "id": _id(),
            "text": text,
            "content_hash": _hash(text),
            "parent_id": parent_id,
            "reason": reason,
            "created_at": _now(),
        }
        connection.execute(
            "INSERT INTO revisions (id, text, content_hash, parent_id, reason, created_at) "
            "VALUES (:id, :text, :content_hash, :parent_id, :reason, :created_at)",
            revision,
        )
        return revision

    def create_revision(
        self, text: str, parent_id: str | None = None, reason: str = "manual"
    ) -> dict[str, Any]:
        if not isinstance(text, str):
            raise WorkflowError("Manuscript text must be a string.")
        if not isinstance(reason, str) or not reason.strip():
            raise WorkflowError("A revision reason is required.")
        try:
            _hash(text)
        except UnicodeEncodeError as error:
            raise WorkflowError("Manuscript text must contain valid Unicode characters.") from error
        with self._connection(write=True) as connection:
            latest = self._latest(connection)
            if parent_id is not None:
                self._require_latest(connection, parent_id)
            else:
                parent_id = latest["id"] if latest else None
            return self._insert_revision(connection, text, parent_id, reason)

    def get_revision(self, revision_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            return self._revision(connection, revision_id)

    def latest_revision(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            return self._latest(connection)

    def list_revisions(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            return [self._row(row) for row in connection.execute("SELECT * FROM revisions ORDER BY sequence")]

    def approve(self, revision_id: str, actor: str = "Local reviewer") -> dict[str, Any]:
        if not isinstance(actor, str) or not actor.strip():
            raise WorkflowError("A reviewer name is required.")
        with self._connection(write=True) as connection:
            revision = self._require_latest(connection, revision_id)
            approval = {
                "id": _id(),
                "revision_id": revision_id,
                "content_hash": revision["content_hash"],
                "actor": actor,
                "approved_at": _now(),
            }
            connection.execute(
                "INSERT INTO approvals (id, revision_id, content_hash, actor, approved_at) "
                "VALUES (:id, :revision_id, :content_hash, :actor, :approved_at)",
                approval,
            )
            return approval

    def approval_for(self, revision_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            self._revision(connection, revision_id)
            return self._row(
                connection.execute(
                    "SELECT * FROM approvals WHERE revision_id = ? ORDER BY sequence DESC LIMIT 1",
                    (revision_id,),
                ).fetchone()
            )

    @staticmethod
    def _locate(text: str, original: str) -> tuple[int | None, int | None, str]:
        start = text.find(original)
        if start < 0:
            return None, None, "missing"
        if text.find(original, start + 1) >= 0:
            return None, None, "ambiguous"
        return start, start + len(original), "unique"

    def save_suggestions(
        self,
        revision_id: str,
        suggestions: list[dict[str, str]],
        mode: str,
        provider_model: str = "",
    ) -> list[dict[str, Any]]:
        if not isinstance(suggestions, list):
            raise WorkflowError("Suggestions must be a list.")
        if not isinstance(mode, str) or not mode.strip() or not isinstance(provider_model, str):
            raise WorkflowError("Suggestion mode and provider must be valid strings.")
        with self._connection(write=True) as connection:
            revision = self._require_latest(connection, revision_id)
            rows = []
            for suggestion in suggestions:
                if not isinstance(suggestion, dict):
                    raise WorkflowError("Each suggestion must be an object.")
                if not all(isinstance(suggestion.get(key), str) for key in ("original", "replacement", "rationale")):
                    raise WorkflowError("Suggestions require original, replacement, and rationale strings.")
                if not suggestion["original"]:
                    raise WorkflowError("An original passage is required; insertion-only suggestions are unsupported.")
                start, end, match_status = self._locate(revision["text"], suggestion["original"])
                row = {
                    "id": _id(),
                    "revision_id": revision_id,
                    "original": suggestion["original"],
                    "replacement": suggestion["replacement"],
                    "rationale": suggestion["rationale"],
                    "start": start,
                    "end": end,
                    "match_status": match_status,
                    "decision": "pending",
                    "mode": mode,
                    "provider_model": provider_model,
                    "created_at": _now(),
                    "decided_at": None,
                    "result_revision_id": None,
                }
                connection.execute(
                    "INSERT INTO suggestions "
                    "(id, revision_id, original, replacement, rationale, start, end, match_status, "
                    "decision, mode, provider_model, created_at, decided_at, result_revision_id) "
                    "VALUES (:id, :revision_id, :original, :replacement, :rationale, :start, :end, "
                    ":match_status, :decision, :mode, :provider_model, :created_at, :decided_at, :result_revision_id)",
                    row,
                )
                rows.append(row)
            return rows

    def list_suggestions(self, revision_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            self._revision(connection, revision_id)
            return [
                self._row(row)
                for row in connection.execute(
                    "SELECT * FROM suggestions WHERE revision_id = ? ORDER BY sequence", (revision_id,)
                )
            ]

    def apply_decisions(self, revision_id: str, decisions: dict[str, str]) -> dict[str, Any]:
        if not isinstance(decisions, dict):
            raise WorkflowError("Decisions must map suggestion IDs to accepted or rejected.")
        if any(not isinstance(key, str) or value not in ("accepted", "rejected") for key, value in decisions.items()):
            raise WorkflowError("Each decision must be accepted or rejected.")
        with self._connection(write=True) as connection:
            revision = self._require_latest(connection, revision_id)
            accepted = []
            for suggestion_id, decision in decisions.items():
                row = self._row(connection.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone())
                if row is None or row["revision_id"] != revision_id:
                    raise WorkflowError("A suggestion does not belong to this revision.")
                if row["decision"] != "pending":
                    raise WorkflowError("A suggestion already has a recorded decision.")
                if decision == "accepted":
                    start, end, match_status = self._locate(revision["text"], row["original"])
                    if match_status != "unique":
                        raise WorkflowError(f"Cannot accept a suggestion whose original passage is {match_status}.")
                    if row["start"] != start or row["end"] != end:
                        raise WorkflowError("The suggestion's passage offsets do not match the manuscript.")
                    accepted.append(row)

            accepted.sort(key=lambda row: (row["start"], row["end"]))
            for left, right in zip(accepted, accepted[1:]):
                if right["start"] < left["end"]:
                    raise WorkflowError("Accepted suggestions overlap. Review and apply non-overlapping edits.")

            result = revision
            if accepted:
                text = revision["text"]
                for row in reversed(accepted):
                    text = text[: row["start"]] + row["replacement"] + text[row["end"] :]
                try:
                    result = self._insert_revision(connection, text, revision_id, "accepted_suggestions")
                except UnicodeEncodeError as error:
                    raise WorkflowError("Replacement text must contain valid Unicode characters.") from error

            decided_at = _now()
            for suggestion_id, decision in decisions.items():
                connection.execute(
                    "UPDATE suggestions SET decision = ?, decided_at = ?, result_revision_id = ? WHERE id = ?",
                    (decision, decided_at, result["id"], suggestion_id),
                )
            return result

    @staticmethod
    def _json(value: dict[str, Any]) -> str:
        if not isinstance(value, dict):
            raise WorkflowError("The record must be a JSON object.")
        try:
            return json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise WorkflowError("The record must contain valid JSON data.") from error

    def save_candidate(self, candidate: dict[str, Any]) -> str:
        if not isinstance(candidate, dict):
            raise WorkflowError("Candidate must be a JSON object.")
        candidate = dict(candidate)
        candidate_id = candidate.get("id", _id())
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise WorkflowError("Candidate ID must be a nonempty string.")
        candidate["id"] = candidate_id
        payload = self._json(candidate)
        with self._connection(write=True) as connection:
            if connection.execute("SELECT 1 FROM candidates WHERE id = ?", (candidate_id,)).fetchone():
                raise WorkflowError("Candidate already exists; saved candidates cannot be replaced.")
            connection.execute(
                "INSERT INTO candidates (id, payload, created_at) VALUES (?, ?, ?)", (candidate_id, payload, _now())
            )
        return candidate_id

    def latest_candidate(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT payload FROM candidates ORDER BY sequence DESC LIMIT 1").fetchone()
            return json.loads(row["payload"]) if row else None

    def list_candidates(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            return [json.loads(row["payload"]) for row in connection.execute("SELECT payload FROM candidates ORDER BY sequence")]

    def save_validation(self, candidate_id: str, report: dict[str, Any]) -> str:
        payload = self._json(report)
        validation_id = _id()
        with self._connection(write=True) as connection:
            if not connection.execute("SELECT 1 FROM candidates WHERE id = ?", (candidate_id,)).fetchone():
                raise WorkflowError("Candidate does not exist.")
            connection.execute(
                "INSERT INTO validations (id, candidate_id, report, created_at) VALUES (?, ?, ?, ?)",
                (validation_id, candidate_id, payload, _now()),
            )
        return validation_id

    def list_validations(self, candidate_id: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as connection:
            query = "SELECT * FROM validations"
            parameters = ()
            if candidate_id is not None:
                query += " WHERE candidate_id = ?"
                parameters = (candidate_id,)
            query += " ORDER BY sequence"
            rows = []
            for row in connection.execute(query, parameters):
                record = self._row(row)
                record["report"] = json.loads(record["report"])
                rows.append(record)
            return rows
