"""RBAC and command-risk policy used before AgentLoop tool dispatch."""

from __future__ import annotations

from agent.protocols.errors import ErrorCode
from agent.protocols.tool import ToolExecutionContext, ToolExecutionDecision
from agent.tools.shell_policy import validate_command

_TOOL_PERMISSIONS = {
    "list_dir": "tool.readonly.use",
    "read_file": "tool.readonly.use",
    "grep": "tool.readonly.use",
    "load_skills": "skill.read",
    "sync_skills": "skill.sync",
    "diagnose_my_recent_activity": "turn.read.own",
}


class RbacToolExecutionPolicy:
    """Map tool names and exec risk categories to actor permission keys."""

    def decide(self, tool_name, args, context: ToolExecutionContext) -> ToolExecutionDecision:
        """Return allow, deny, or explicit-confirmation required."""

        if tool_name == "exec":
            return self._decide_exec(args, context)
        permission_key = _TOOL_PERMISSIONS.get(tool_name, "tool.readonly.use")
        if not context.actor.has_permission(permission_key):
            return _denied(permission_key)
        return ToolExecutionDecision(
            action="allow",
            code="ALLOWED",
            message="Tool execution allowed",
            permission_key=permission_key,
        )

    @staticmethod
    def _decide_exec(args, context: ToolExecutionContext) -> ToolExecutionDecision:
        command = args.get("command") if isinstance(args, dict) else ""
        result = validate_command(command)
        if not result.allowed:
            return ToolExecutionDecision(
                action="deny",
                code=result.code,
                message=result.message,
                permission_key=result.required_permission,
                risk_level=result.risk_level,
                risk_category=result.risk_category,
            )
        permission_key = result.required_permission or "tool.exec.safe"
        if not context.actor.has_permission(permission_key):
            return ToolExecutionDecision(
                action="deny",
                code=ErrorCode.AUTH_PERMISSION_DENIED,
                message="Permission denied",
                permission_key=permission_key,
                risk_level=result.risk_level,
                risk_category=result.risk_category,
            )
        return ToolExecutionDecision(
            action="confirm" if result.requires_confirmation else "allow",
            code=result.code,
            message=result.message or "Tool execution allowed",
            permission_key=permission_key,
            risk_level=result.risk_level,
            risk_category=result.risk_category,
            audit_metadata={"command_category": result.category},
        )


def _denied(permission_key: str) -> ToolExecutionDecision:
    return ToolExecutionDecision(
        action="deny",
        code=ErrorCode.AUTH_PERMISSION_DENIED,
        message="Permission denied",
        permission_key=permission_key,
    )
