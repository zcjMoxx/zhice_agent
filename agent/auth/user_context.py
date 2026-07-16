"""Filesystem user-context resolver for Web and external-channel actors."""

from __future__ import annotations

import re
from pathlib import Path

from agent.protocols.auth import UserContext

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class FilesystemUserContextResolver:
    """Resolve workspace-operator or isolated user filesystem contexts."""

    def __init__(
        self,
        contexts_dir: Path | str,
        *,
        workspace_dir: Path | str | None = None,
    ):
        self.contexts_dir = Path(contexts_dir).expanduser().resolve()
        self.workspace_dir = (
            Path(workspace_dir).expanduser().resolve()
            if workspace_dir is not None
            else self.contexts_dir.parent
        )
        self.users_dir = self.contexts_dir / "users"
        self.shared_readonly_dir = self.contexts_dir / "shared" / "readonly"

    def resolve(self, user_id: str, *, use_workspace_context: bool = False) -> UserContext:
        """Return the workspace operator context or one isolated user context."""

        if not _USER_ID_RE.fullmatch(str(user_id)):
            raise ValueError("invalid internal user id")
        if use_workspace_context:
            root_dir = self.workspace_dir
            files_dir = self.workspace_dir
            sessions_dir = self.contexts_dir / "sessions"
            sessions_meta_dir = self.contexts_dir / "sessions_meta"
            memory_dir = self.contexts_dir / "memory"
            directories = (
                root_dir,
                self.shared_readonly_dir,
                sessions_dir,
                sessions_meta_dir,
                memory_dir,
            )
        else:
            root_dir = (self.users_dir / user_id).resolve()
            if not _is_relative_to(root_dir, self.users_dir):
                raise ValueError("user context is outside contexts/users")
            files_dir = root_dir / "files"
            sessions_dir = root_dir / "sessions"
            sessions_meta_dir = root_dir / "sessions_meta"
            memory_dir = root_dir / "memory"
            directories = (
                self.users_dir,
                self.shared_readonly_dir,
                root_dir,
                files_dir,
                sessions_dir,
                sessions_meta_dir,
                memory_dir,
            )
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        return UserContext(
            user_id=user_id,
            root_dir=root_dir,
            files_dir=files_dir,
            sessions_dir=sessions_dir,
            sessions_meta_dir=sessions_meta_dir,
            memory_dir=memory_dir,
            shared_readonly_dir=self.shared_readonly_dir,
        )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
