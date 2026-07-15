"""Filesystem user-context resolver for Web and external-channel actors."""

from __future__ import annotations

import re
from pathlib import Path

from agent.protocols.auth import UserContext

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class FilesystemUserContextResolver:
    """Resolve user contexts below contexts/users/{stable-user-id}."""

    def __init__(self, contexts_dir: Path | str):
        self.contexts_dir = Path(contexts_dir).expanduser().resolve()
        self.users_dir = self.contexts_dir / "users"
        self.shared_readonly_dir = self.contexts_dir / "shared" / "readonly"

    def resolve(self, user_id: str, *, use_global_sessions: bool = False) -> UserContext:
        """Create and return the fixed directory set for one internal user."""

        if not _USER_ID_RE.fullmatch(str(user_id)):
            raise ValueError("invalid internal user id")
        root_dir = (self.users_dir / user_id).resolve()
        if not _is_relative_to(root_dir, self.users_dir):
            raise ValueError("user context is outside contexts/users")
        files_dir = root_dir / "files"
        sessions_dir = self.contexts_dir / "sessions" if use_global_sessions else root_dir / "sessions"
        sessions_meta_dir = (
            self.contexts_dir / "sessions_meta"
            if use_global_sessions
            else root_dir / "sessions_meta"
        )
        for directory in (
            self.users_dir,
            self.shared_readonly_dir,
            root_dir,
            files_dir,
            sessions_dir,
            sessions_meta_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return UserContext(
            user_id=user_id,
            root_dir=root_dir,
            files_dir=files_dir,
            sessions_dir=sessions_dir,
            sessions_meta_dir=sessions_meta_dir,
            shared_readonly_dir=self.shared_readonly_dir,
        )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
