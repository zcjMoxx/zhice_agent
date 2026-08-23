"""Current-state authorization checks for workflow operations."""

from dataclasses import dataclass

from agent.protocols.auth import ActorContext
from agent.workflows.schemas import WorkflowDefinitionV1


@dataclass(frozen=True)
class WorkflowAuthorizationPolicy:
    query_tools: frozenset[str] = frozenset()
    action_tools: frozenset[str] = frozenset()

    def require_owner(self, actor: ActorContext, definition: WorkflowDefinitionV1) -> None:
        if actor.user_id != definition.owner_user_id:
            raise PermissionError("WORKFLOW_ACCESS_DENIED")
        self.require(actor, "workflow.use")

    @staticmethod
    def require(actor: ActorContext, permission: str) -> None:
        if permission in {"workflow.use", "workflow.schedule", "workflow.notify.self"}:
            normal_roles = {"viewer", "developer", "admin", "owner"}
            if actor.role_keys.intersection(normal_roles):
                return
        if not actor.has_permission(permission):
            raise PermissionError("WORKFLOW_PERMISSION_REVOKED")

    def authorize_tool(self, actor: ActorContext, tool_name: str, *, action: bool) -> None:
        allowed = self.action_tools if action else self.query_tools
        if tool_name not in allowed:
            raise PermissionError("WORKFLOW_TOOL_NOT_ALLOWED")
        if action:
            self.require(actor, "workflow.external.action")
