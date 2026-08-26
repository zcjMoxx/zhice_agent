"""Actor-scoped SQLite persistence for private travel plans."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.applications.travel.schemas import TravelPlanV1


@dataclass(frozen=True)
class TravelPlanSummary:
    """Metadata-only travel plan projection safe for list and diagnostics."""

    plan_id: str
    owner_user_id: str
    source_session_id: str
    source_turn_id: str
    schema_version: str
    title: str
    destination_summary: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "owner_user_id": self.owner_user_id,
            "source_session_id": self.source_session_id,
            "source_turn_id": self.source_turn_id,
            "schema_version": self.schema_version,
            "title": self.title,
            "destination_summary": self.destination_summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class TravelCandidateReview:
    """Bounded user-review state for feasible optimizer candidates."""

    session_id: str
    owner_user_id: str
    turn_id: str
    status: str
    recommended_candidate_id: str
    selected_candidate_id: str
    candidates: tuple[dict[str, Any], ...]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "recommended_candidate_id": self.recommended_candidate_id,
            "selected_candidate_id": self.selected_candidate_id,
            "candidates": [dict(item) for item in self.candidates],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class TravelPlanDraft:
    """Server-owned failed finalizer draft isolated by owner and Session."""

    session_id: str
    owner_user_id: str
    revision: str
    selected_candidate_id: str
    plan: dict[str, Any]
    created_at: str
    updated_at: str

    def to_attempt(self) -> dict[str, Any]:
        return {
            "plan": dict(self.plan),
            "draft_revision": self.revision,
            "selected_candidate_id": self.selected_candidate_id,
        }


class TravelPlanStoreError(RuntimeError):
    """Structured storage failure with a public-safe code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class TravelPlanStore:
    """Persist plans beneath exactly one actor context root."""

    def __init__(self, actor_context_root: Path | str):
        self.actor_context_root = Path(actor_context_root).expanduser().resolve()
        self.travel_dir = (self.actor_context_root / "travel").resolve()
        if not _is_relative_to(self.travel_dir, self.actor_context_root):
            raise ValueError("travel store is outside actor context root")
        self.db_path = self.travel_dir / "plans.sqlite3"
        self.travel_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save(
        self,
        plan: TravelPlanV1,
        *,
        owner_user_id: str,
        source_session_id: str,
        source_turn_id: str,
        title: str,
        expected_draft_revision: str | None = None,
    ) -> TravelPlanV1:
        """Insert one already-validated plan, never overwriting another plan id."""

        if not owner_user_id:
            raise TravelPlanStoreError("TRAVEL_PLAN_ACCESS_DENIED", "Plan owner is required.")
        plan_id = str(plan.data.get("plan_id") or "").strip()
        if not plan_id:
            raise TravelPlanStoreError("TRAVEL_PLAN_SCHEMA_INVALID", "Plan id is required.")
        if plan.data.get("owner_user_id") != owner_user_id:
            raise TravelPlanStoreError("TRAVEL_PLAN_ACCESS_DENIED", "Plan owner does not match.")
        request = plan.request
        destination_summary = " / ".join(request.destinations)
        now = _utc_now()
        encoded = json.dumps(
            plan.to_dict(), ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if expected_draft_revision is not None:
                    draft = connection.execute(
                        """
                        SELECT revision FROM travel_plan_drafts
                        WHERE session_id=? AND owner_user_id=?
                        """,
                        (source_session_id, owner_user_id),
                    ).fetchone()
                    if (
                        draft is None
                        or str(draft["revision"]) != expected_draft_revision
                    ):
                        connection.rollback()
                        raise TravelPlanStoreError(
                            "TRAVEL_PLAN_DRAFT_CONFLICT",
                            "Travel plan draft changed before final persistence.",
                        )
                connection.execute(
                    """
                    INSERT INTO travel_plans (
                      id, owner_user_id, source_session_id, source_turn_id,
                      schema_version, title, destination_summary, plan_json,
                      created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id,
                        owner_user_id,
                        source_session_id,
                        source_turn_id,
                        str(plan.data["schema_version"]),
                        _safe_title(title, request),
                        destination_summary[:300],
                        encoded,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    "DELETE FROM travel_plan_drafts WHERE session_id=? AND owner_user_id=?",
                    (source_session_id, owner_user_id),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise TravelPlanStoreError(
                "TRAVEL_PLAN_SCHEMA_INVALID", "Travel plan id already exists."
            ) from exc
        except sqlite3.Error as exc:
            raise TravelPlanStoreError(
                "TRAVEL_SOURCE_UNAVAILABLE", "Travel plan storage is unavailable."
            ) from exc
        return plan

    def list(self, owner_user_id: str, *, limit: int = 50) -> list[TravelPlanSummary]:
        """List metadata for exactly one owner without loading plan bodies."""

        bounded = max(1, min(int(limit), 100))
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT id, owner_user_id, source_session_id, source_turn_id,
                           schema_version, title, destination_summary, created_at, updated_at
                    FROM travel_plans
                    WHERE owner_user_id = ?
                    ORDER BY updated_at DESC, id DESC
                    LIMIT ?
                    """,
                    (owner_user_id, bounded),
                ).fetchall()
        except sqlite3.Error as exc:
            raise TravelPlanStoreError(
                "TRAVEL_SOURCE_UNAVAILABLE", "Travel plan storage is unavailable."
            ) from exc
        return [_summary(row) for row in rows]

    def get(self, owner_user_id: str, plan_id: str) -> TravelPlanV1:
        """Load a plan only when both its id and trusted owner match."""

        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT plan_json FROM travel_plans WHERE id = ? AND owner_user_id = ?",
                    (plan_id, owner_user_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise TravelPlanStoreError(
                "TRAVEL_SOURCE_UNAVAILABLE", "Travel plan storage is unavailable."
            ) from exc
        if row is None:
            raise TravelPlanStoreError("TRAVEL_PLAN_NOT_FOUND", "Travel plan was not found.")
        try:
            value = json.loads(str(row["plan_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise TravelPlanStoreError(
                "TRAVEL_PLAN_SCHEMA_INVALID", "Stored travel plan is invalid."
            ) from exc
        try:
            return TravelPlanV1.from_dict(
                value,
                max_evidence_items=100,
                max_plan_bytes=2 * 1024 * 1024,
            )
        except Exception as exc:
            raise TravelPlanStoreError(
                "TRAVEL_PLAN_SCHEMA_INVALID", "Stored travel plan is invalid."
            ) from exc

    def delete(self, owner_user_id: str, plan_id: str) -> str:
        """Delete one plan and return its associated Session id."""

        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT source_session_id FROM travel_plans WHERE id = ? AND owner_user_id = ?",
                    (plan_id, owner_user_id),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    raise TravelPlanStoreError(
                        "TRAVEL_PLAN_NOT_FOUND", "Travel plan was not found."
                    )
                cursor = connection.execute(
                    "DELETE FROM travel_plans WHERE id = ? AND owner_user_id = ?",
                    (plan_id, owner_user_id),
                )
                connection.execute(
                    "DELETE FROM travel_candidate_reviews WHERE session_id=? AND owner_user_id=?",
                    (str(row["source_session_id"]), owner_user_id),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise TravelPlanStoreError(
                "TRAVEL_SOURCE_UNAVAILABLE", "Travel plan storage is unavailable."
            ) from exc
        if cursor.rowcount != 1:
            raise TravelPlanStoreError("TRAVEL_PLAN_NOT_FOUND", "Travel plan was not found.")
        return str(row["source_session_id"])

    def save_candidate_review(
        self,
        owner_user_id: str,
        session_id: str,
        turn_id: str,
        candidates: list[dict[str, Any]],
        recommended_candidate_id: str,
    ) -> TravelCandidateReview:
        """Replace one Session's pending review with bounded optimizer summaries."""

        if not owner_user_id or not session_id or not turn_id:
            raise TravelPlanStoreError("TRAVEL_CANDIDATE_REVIEW_INVALID", "Candidate review identity is incomplete.")
        candidate_ids = {str(item.get("candidate_id") or "") for item in candidates}
        if not 1 <= len(candidates) <= 3 or recommended_candidate_id not in candidate_ids:
            raise TravelPlanStoreError("TRAVEL_CANDIDATE_REVIEW_INVALID", "Candidate review is invalid.")
        now = _utc_now()
        encoded = json.dumps(candidates, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > 256 * 1024:
            raise TravelPlanStoreError("TRAVEL_CANDIDATE_REVIEW_INVALID", "Candidate review is too large.")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO travel_candidate_reviews (
                      session_id, owner_user_id, turn_id, status,
                      recommended_candidate_id, selected_candidate_id,
                      candidates_json, created_at, updated_at
                    ) VALUES (?, ?, ?, 'pending', ?, '', ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                      owner_user_id=excluded.owner_user_id,
                      turn_id=excluded.turn_id,
                      status='pending',
                      recommended_candidate_id=excluded.recommended_candidate_id,
                      selected_candidate_id='',
                      candidates_json=excluded.candidates_json,
                      updated_at=excluded.updated_at
                    """,
                    (session_id, owner_user_id, turn_id, recommended_candidate_id, encoded, now, now),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise TravelPlanStoreError("TRAVEL_SOURCE_UNAVAILABLE", "Candidate review storage is unavailable.") from exc
        review = self.get_candidate_review(owner_user_id, session_id)
        if review is None:
            raise TravelPlanStoreError("TRAVEL_SOURCE_UNAVAILABLE", "Candidate review storage is unavailable.")
        return review

    def get_candidate_review(
        self, owner_user_id: str, session_id: str
    ) -> TravelCandidateReview | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT session_id, owner_user_id, turn_id, status,
                           recommended_candidate_id, selected_candidate_id,
                           candidates_json, created_at, updated_at
                    FROM travel_candidate_reviews
                    WHERE session_id = ? AND owner_user_id = ?
                    """,
                    (session_id, owner_user_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise TravelPlanStoreError("TRAVEL_SOURCE_UNAVAILABLE", "Candidate review storage is unavailable.") from exc
        if row is None:
            return None
        try:
            candidates = json.loads(str(row["candidates_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise TravelPlanStoreError("TRAVEL_CANDIDATE_REVIEW_INVALID", "Stored candidate review is invalid.") from exc
        if not isinstance(candidates, list):
            raise TravelPlanStoreError("TRAVEL_CANDIDATE_REVIEW_INVALID", "Stored candidate review is invalid.")
        return TravelCandidateReview(
            session_id=str(row["session_id"]),
            owner_user_id=str(row["owner_user_id"]),
            turn_id=str(row["turn_id"]),
            status=str(row["status"]),
            recommended_candidate_id=str(row["recommended_candidate_id"]),
            selected_candidate_id=str(row["selected_candidate_id"]),
            candidates=tuple(candidates),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def select_candidate(
        self, owner_user_id: str, session_id: str, candidate_id: str
    ) -> TravelCandidateReview:
        review = self.get_candidate_review(owner_user_id, session_id)
        if review is None:
            raise TravelPlanStoreError("TRAVEL_CANDIDATE_REVIEW_NOT_FOUND", "Candidate review was not found.")
        valid_ids = {str(item.get("candidate_id") or "") for item in review.candidates}
        if candidate_id not in valid_ids:
            raise TravelPlanStoreError("TRAVEL_CANDIDATE_SELECTION_INVALID", "Selected candidate is not available.")
        now = _utc_now()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE travel_candidate_reviews
                    SET status='selected', selected_candidate_id=?, updated_at=?
                    WHERE session_id=? AND owner_user_id=?
                    """,
                    (candidate_id, now, session_id, owner_user_id),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise TravelPlanStoreError("TRAVEL_SOURCE_UNAVAILABLE", "Candidate review storage is unavailable.") from exc
        selected = self.get_candidate_review(owner_user_id, session_id)
        if selected is None:
            raise TravelPlanStoreError("TRAVEL_CANDIDATE_REVIEW_NOT_FOUND", "Candidate review was not found.")
        return selected

    def clear_candidate_review(self, owner_user_id: str, session_id: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM travel_candidate_reviews WHERE session_id=? AND owner_user_id=?",
                    (session_id, owner_user_id),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise TravelPlanStoreError("TRAVEL_SOURCE_UNAVAILABLE", "Candidate review storage is unavailable.") from exc

    def save_draft(
        self,
        owner_user_id: str,
        session_id: str,
        plan: dict[str, Any],
        revision: str,
        selected_candidate_id: str,
        *,
        expected_revision: str | None,
    ) -> TravelPlanDraft:
        """Create or compare-and-swap one server-owned finalizer draft."""

        if not owner_user_id or not session_id:
            raise TravelPlanStoreError(
                "TRAVEL_PLAN_ACCESS_DENIED", "Travel plan draft identity is incomplete."
            )
        if not revision.startswith("sha256:") or len(revision) != 71:
            raise TravelPlanStoreError(
                "TRAVEL_PLAN_DRAFT_INVALID", "Travel plan draft revision is invalid."
            )
        try:
            encoded = json.dumps(
                plan, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as exc:
            raise TravelPlanStoreError(
                "TRAVEL_PLAN_DRAFT_INVALID", "Travel plan draft is not valid JSON."
            ) from exc
        now = _utc_now()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if expected_revision is None:
                    connection.execute(
                        """
                        INSERT INTO travel_plan_drafts (
                          session_id, owner_user_id, revision, selected_candidate_id,
                          plan_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            owner_user_id,
                            revision,
                            selected_candidate_id,
                            encoded,
                            now,
                            now,
                        ),
                    )
                else:
                    cursor = connection.execute(
                        """
                        UPDATE travel_plan_drafts
                        SET revision=?, selected_candidate_id=?, plan_json=?, updated_at=?
                        WHERE session_id=? AND owner_user_id=? AND revision=?
                        """,
                        (
                            revision,
                            selected_candidate_id,
                            encoded,
                            now,
                            session_id,
                            owner_user_id,
                            expected_revision,
                        ),
                    )
                    if cursor.rowcount != 1:
                        connection.rollback()
                        raise TravelPlanStoreError(
                            "TRAVEL_PLAN_DRAFT_CONFLICT",
                            "Travel plan draft changed before the repair was saved.",
                        )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise TravelPlanStoreError(
                "TRAVEL_PLAN_DRAFT_CONFLICT",
                "A server-owned travel plan draft already exists for this Session.",
            ) from exc
        except sqlite3.Error as exc:
            raise TravelPlanStoreError(
                "TRAVEL_SOURCE_UNAVAILABLE", "Travel plan draft storage is unavailable."
            ) from exc
        saved = self.get_draft(owner_user_id, session_id)
        if saved is None:
            raise TravelPlanStoreError(
                "TRAVEL_SOURCE_UNAVAILABLE", "Travel plan draft storage is unavailable."
            )
        return saved

    def get_draft(self, owner_user_id: str, session_id: str) -> TravelPlanDraft | None:
        """Load one draft only when its trusted owner and Session both match."""

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT session_id, owner_user_id, revision, selected_candidate_id,
                           plan_json, created_at, updated_at
                    FROM travel_plan_drafts
                    WHERE session_id=? AND owner_user_id=?
                    """,
                    (session_id, owner_user_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise TravelPlanStoreError(
                "TRAVEL_SOURCE_UNAVAILABLE", "Travel plan draft storage is unavailable."
            ) from exc
        if row is None:
            return None
        try:
            plan = json.loads(str(row["plan_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise TravelPlanStoreError(
                "TRAVEL_PLAN_DRAFT_INVALID", "Stored travel plan draft is invalid."
            ) from exc
        if not isinstance(plan, dict):
            raise TravelPlanStoreError(
                "TRAVEL_PLAN_DRAFT_INVALID", "Stored travel plan draft is invalid."
            )
        return TravelPlanDraft(
            session_id=str(row["session_id"]),
            owner_user_id=str(row["owner_user_id"]),
            revision=str(row["revision"]),
            selected_candidate_id=str(row["selected_candidate_id"]),
            plan=plan,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def clear_draft(self, owner_user_id: str, session_id: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM travel_plan_drafts WHERE session_id=? AND owner_user_id=?",
                    (session_id, owner_user_id),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise TravelPlanStoreError(
                "TRAVEL_SOURCE_UNAVAILABLE", "Travel plan draft storage is unavailable."
            ) from exc

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.executescript(
                    """
                    PRAGMA journal_mode = WAL;
                    PRAGMA foreign_keys = ON;
                    CREATE TABLE IF NOT EXISTS travel_plans (
                      id TEXT PRIMARY KEY,
                      owner_user_id TEXT NOT NULL,
                      source_session_id TEXT NOT NULL,
                      source_turn_id TEXT NOT NULL,
                      schema_version TEXT NOT NULL,
                      title TEXT NOT NULL,
                      destination_summary TEXT NOT NULL,
                      plan_json TEXT NOT NULL,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_travel_plans_owner_updated
                      ON travel_plans(owner_user_id, updated_at DESC);
                    CREATE TABLE IF NOT EXISTS travel_candidate_reviews (
                      session_id TEXT PRIMARY KEY,
                      owner_user_id TEXT NOT NULL,
                      turn_id TEXT NOT NULL,
                      status TEXT NOT NULL,
                      recommended_candidate_id TEXT NOT NULL,
                      selected_candidate_id TEXT NOT NULL,
                      candidates_json TEXT NOT NULL,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_travel_candidate_reviews_owner
                      ON travel_candidate_reviews(owner_user_id, updated_at DESC);
                    CREATE TABLE IF NOT EXISTS travel_plan_drafts (
                      session_id TEXT NOT NULL,
                      owner_user_id TEXT NOT NULL,
                      revision TEXT NOT NULL,
                      selected_candidate_id TEXT NOT NULL,
                      plan_json TEXT NOT NULL,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL,
                      PRIMARY KEY (session_id, owner_user_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_travel_plan_drafts_owner
                      ON travel_plan_drafts(owner_user_id, updated_at DESC);
                    """
                )
        except sqlite3.Error as exc:
            raise TravelPlanStoreError(
                "TRAVEL_SOURCE_UNAVAILABLE", "Travel plan storage could not be initialized."
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection


def _summary(row: sqlite3.Row) -> TravelPlanSummary:
    return TravelPlanSummary(
        plan_id=str(row["id"]),
        owner_user_id=str(row["owner_user_id"]),
        source_session_id=str(row["source_session_id"]),
        source_turn_id=str(row["source_turn_id"]),
        schema_version=str(row["schema_version"]),
        title=str(row["title"]),
        destination_summary=str(row["destination_summary"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _safe_title(title: str, request) -> str:
    normalized = " ".join(str(title or "").split())
    if normalized:
        return normalized[:200]
    destinations = " / ".join(request.destinations)
    return f"{request.origin} → {destinations} {request.duration_days} 日旅行计划"[:200]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
