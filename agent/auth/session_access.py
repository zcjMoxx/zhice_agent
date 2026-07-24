"""Actor-aware session ownership and JSONL access service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from agent.auth.store import AuthStoreError, SQLiteAuthStore
from agent.protocols.auth import ActorContext, UserContext
from agent.protocols.errors import ErrorCode
from agent.protocols.session import SessionContext, SessionState, SessionSummary
from agent.session.jsonl_store import JsonlSessionStore, validate_session_id


class SessionAccessError(RuntimeError):
    """Stable session authorization/resource failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.code = str(code)
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})


@dataclass(frozen=True)
class ResolvedSession:
    """Authorized session store and user filesystem context."""

    session_id: str
    owner_user_id: str
    context: UserContext
    store: JsonlSessionStore
    created: bool = False

    def model_context(self) -> SessionContext:
        """Return the protocol-only context used by metadata services."""

        return SessionContext(
            owner_user_id=self.owner_user_id,
            sessions_dir=self.context.sessions_dir,
            sessions_meta_dir=self.context.sessions_meta_dir,
            files_dir=self.context.files_dir,
            shared_readonly_dir=self.context.shared_readonly_dir,
        )


class SessionAccessService:
    """Keep user ownership out of JsonlSessionStore itself."""

    def __init__(self, store: SQLiteAuthStore, user_contexts):
        self.store = store
        self.user_contexts = user_contexts

    def ensure_session(
        self,
        actor: ActorContext,
        session_id: str,
        *,
        channel: str,
        conversation_type: str = "",
        external_chat_id: str = "",
        external_thread_id: str = "",
        write: bool = False,
    ) -> ResolvedSession:
        """Create or resolve an actor-owned session."""

        self._require_user(actor)
        validate_session_id(session_id)
        row = self.store.session_index_get(session_id)
        created = False
        if row is None:
            try:
                self.store.session_index_create(
                    session_id=session_id,
                    owner_user_id=str(actor.user_id),
                    channel=channel,
                    conversation_type=conversation_type,
                    external_chat_id=external_chat_id,
                    external_thread_id=external_thread_id,
                )
            except AuthStoreError as exc:
                raise self._not_found() from exc
            row = self.store.session_index_get(session_id)
            created = True
        if row is None or not self._can_access(actor, str(row["owner_user_id"])):
            raise self._not_found()
        return self._resolved(str(row["owner_user_id"]), session_id, created=created)

    def resolve_session(
        self,
        actor: ActorContext,
        session_id: str,
        *,
        write: bool = False,
        delete: bool = False,
    ) -> ResolvedSession:
        """Resolve an existing visible session and enforce the requested mutation permission."""

        self._require_user(actor)
        validate_session_id(session_id)
        row = self.store.session_index_get(session_id)
        if row is None or not self._can_access(actor, str(row["owner_user_id"])):
            raise self._not_found()
        owner = str(row["owner_user_id"])
        return self._resolved(owner, session_id)

    def list_sessions(self, actor: ActorContext) -> list[SessionSummary]:
        """List the actor's own sessions for the normal chat surface."""

        self._require_user(actor)
        self._reconcile_owner_cli_sessions(actor)
        rows = self.store.session_index_list(str(actor.user_id))
        summaries: list[SessionSummary] = []
        for row in rows:
            owner = str(row["owner_user_id"])
            session_id = str(row["session_id"])
            summary = _find_summary(self._resolved(owner, session_id).store.list_sessions(), session_id)
            if summary is None:
                summary = SessionSummary(
                    session_id=session_id,
                    preview=str(row["preview"] or "(empty)"),
                    updated_at=_parse_timestamp(str(row["updated_at"])),
                    message_count=int(row["message_count"]),
                    title=str(row["title"]),
                )
            summaries.append(
                replace(
                    summary,
                    channel=str(row["channel"]),
                    conversation_type=str(row.get("conversation_type") or ""),
                    continuation_mode=_continuation_mode(row),
                )
            )
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def assert_chat_continuation_allowed(
        self,
        actor: ActorContext,
        session_id: str,
        *,
        request_channel: str,
    ) -> None:
        """Reject private-control writes into public external conversation history."""

        self._require_user(actor)
        row = self.store.session_index_get(session_id)
        if row is None:
            return
        if not self._can_access(actor, str(row["owner_user_id"])):
            raise self._not_found()
        if _continuation_mode(row) == "fork_only" and request_channel != str(row["channel"]):
            raise SessionAccessError(
                ErrorCode.SESSION_CHANNEL_READ_ONLY,
                "This group-channel session is read-only here. Continue from a Web copy instead.",
                status_code=409,
                details={"continuation_mode": "fork_only"},
            )

    def fork_to_web(self, actor: ActorContext, source_session_id: str) -> SessionSummary:
        """Copy one public-channel history into a new private Web session."""

        source = self.resolve_session(actor, source_session_id)
        row = self.store.session_index_get(source_session_id)
        if row is None or _continuation_mode(row) != "fork_only":
            raise SessionAccessError(
                ErrorCode.SESSION_CHANNEL_READ_ONLY,
                "Only read-only group-channel sessions need a Web copy.",
                status_code=409,
            )
        target_session_id = "session-fork-" + uuid.uuid4().hex
        target = self.ensure_session(actor, target_session_id, channel="web", write=True)
        source_state = source.store.load(source_session_id)
        if source_state.messages:
            target.store.append(target_session_id, list(source_state.messages))
        source_title = str(row.get("title") or "").strip()
        if source_title:
            target.store.rename(target_session_id, f"{source_title} (Web copy)")
        self.refresh_index(actor, target_session_id)
        return next(
            summary
            for summary in self.list_sessions(actor)
            if summary.session_id == target_session_id
        )

    def _reconcile_owner_cli_sessions(self, actor: ActorContext) -> None:
        """Index unowned global CLI sessions for Owner without copying their files."""

        user = self.store.get_user(str(actor.user_id))
        if "owner" not in user.role_keys:
            return
        context = self.user_contexts.resolve(user.id, use_workspace_context=True)
        for summary in JsonlSessionStore(context.sessions_dir).list_sessions():
            if self.store.session_index_get(summary.session_id) is not None:
                continue
            try:
                self.store.session_index_create(
                    session_id=summary.session_id,
                    owner_user_id=user.id,
                    channel="cli_legacy",
                )
            except AuthStoreError:
                continue
            self.store.session_index_update(
                summary.session_id,
                title=summary.title,
                preview=summary.preview,
                message_count=summary.message_count,
                updated_at=(
                    datetime.fromtimestamp(summary.updated_at, UTC).isoformat(timespec="seconds")
                    if summary.updated_at > 0
                    else None
                ),
            )

    def load_session(self, actor: ActorContext, session_id: str) -> SessionState:
        """Load one visible existing session."""

        return self.resolve_session(actor, session_id).store.load(session_id)

    def rename_session(self, actor: ActorContext, session_id: str, title: str) -> SessionSummary:
        """Rename an owned or globally managed session."""

        resolved = self.resolve_session(actor, session_id, write=True)
        resolved.store.rename(session_id, title)
        self.refresh_index(actor, session_id)
        summary = _find_summary(resolved.store.list_sessions(), session_id)
        if summary is None:
            state = resolved.store.load(session_id)
            return SessionSummary(session_id, "(empty)", 0.0, len(state.messages), title=title)
        return summary

    def clear_session(self, actor: ActorContext, session_id: str) -> None:
        """Clear messages while preserving metadata and ownership."""

        resolved = self.resolve_session(actor, session_id, write=True)
        resolved.store.clear(session_id)
        self.refresh_index(actor, session_id)

    def delete_session(self, actor: ActorContext, session_id: str) -> None:
        """Delete JSONL, metadata, and the owner index row."""

        resolved = self.resolve_session(actor, session_id, delete=True)
        row = self.store.session_index_get(session_id)
        if row is not None and str(row.get("channel") or "") not in {
            "",
            "web",
            "cli",
            "cli_legacy",
        }:
            raise SessionAccessError(
                ErrorCode.SESSION_CHANNEL_READ_ONLY,
                "External-channel session history cannot be deleted while its route is retained.",
                status_code=409,
            )
        resolved.store.delete(session_id)
        self.store.session_index_delete(session_id)

    def refresh_index(self, actor: ActorContext, session_id: str) -> None:
        """Recompute list fields from the JSONL source of truth."""

        resolved = self.resolve_session(actor, session_id, write=True)
        summary = _find_summary(resolved.store.list_sessions(), session_id)
        state = resolved.store.load(session_id)
        title = str(state.metadata.get("title") or "")
        self.store.session_index_update(
            session_id,
            title=title,
            preview=summary.preview if summary else "(empty)",
            message_count=len(state.messages),
            updated_at=(
                datetime.fromtimestamp(summary.updated_at, UTC).isoformat(timespec="seconds")
                if summary and summary.updated_at > 0
                else None
            ),
        )

    def _resolved(
        self,
        owner_user_id: str,
        session_id: str,
        *,
        created: bool = False,
    ) -> ResolvedSession:
        owner = self.store.get_user(owner_user_id)
        is_owner = "owner" in owner.role_keys
        context = self.user_contexts.resolve(
            owner_user_id,
            use_workspace_context=is_owner,
        )
        return ResolvedSession(
            session_id=session_id,
            owner_user_id=owner_user_id,
            context=context,
            store=JsonlSessionStore(context.sessions_dir),
            created=created,
        )

    @staticmethod
    def _can_access(actor: ActorContext, owner_user_id: str) -> bool:
        return actor.user_id == owner_user_id or actor.has_permission("session.manage.any")

    @staticmethod
    def _require_user(actor: ActorContext) -> None:
        if actor.user_id is None:
            raise SessionAccessError(
                ErrorCode.AUTH_ACCOUNT_REQUIRED,
                "Database user is required",
                status_code=403,
            )

    @staticmethod
    def _not_found() -> SessionAccessError:
        return SessionAccessError(ErrorCode.SESSION_NOT_FOUND, "Session not found", status_code=404)


def _find_summary(summaries: list[SessionSummary], session_id: str) -> SessionSummary | None:
    return next((summary for summary in summaries if summary.session_id == session_id), None)


def _parse_timestamp(value: str) -> float:
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return 0.0
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.timestamp()


def _continuation_mode(row: dict[str, object]) -> str:
    if str(row.get("channel") or "") == "qq" and str(
        row.get("conversation_type") or ""
    ) == "group":
        return "fork_only"
    return "writable"
