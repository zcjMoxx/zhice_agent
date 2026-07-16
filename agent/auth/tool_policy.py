"""RBAC and command-risk policy used before AgentLoop tool dispatch."""

from __future__ import annotations

import hashlib

from agent.protocols.errors import ErrorCode
from agent.protocols.tool import ToolExecutionContext, ToolExecutionDecision
from agent.tools.shell_policy import validate_command

_PRIVILEGED_TOOL_PERMISSIONS = {
    "sync_skills": "skill.sync",
}


class RbacToolExecutionPolicy:
    """Map tool names and exec risk categories to actor permission keys."""

    def decide(self, tool_name, args, context: ToolExecutionContext) -> ToolExecutionDecision:
        """Return allow, deny, or explicit-confirmation required."""

        if tool_name == "exec":
            return self._decide_exec(args, context)
        if tool_name == "memory_write":
            return self._decide_memory_write(args, context)
        permission_key = _PRIVILEGED_TOOL_PERMISSIONS.get(tool_name, "")
        if permission_key and not context.actor.has_permission(permission_key):
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
        permission_key = result.required_permission or ""
        if permission_key and not context.actor.has_permission(permission_key):
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

    @staticmethod
    def _decide_memory_write(args, context: ToolExecutionContext) -> ToolExecutionDecision:
        authorization = str(args.get("authorization") or "") if isinstance(args, dict) else ""
        if authorization not in {"user_explicit", "user_confirmed"}:
            return ToolExecutionDecision(
                action="deny",
                code="MEMORY_USER_AUTHORIZATION_REQUIRED",
                message="Memory write requires conversational user authorization.",
                permission_key="",
                risk_level="low",
                risk_category="memory_write",
            )
        content = args.get("content") if isinstance(args, dict) else ""
        return ToolExecutionDecision(
            action="allow",
            code="MEMORY_WRITE_AUTHORIZED",
            message="Memory write authorized by the user conversation.",
            permission_key="",
            risk_level="low",
            risk_category="memory_write",
            audit_metadata={
                "operation": str(args.get("operation") or "") if isinstance(args, dict) else "",
                "category": str(args.get("category") or "") if isinstance(args, dict) else "",
                "authorization": authorization,
                "content_length": len(content) if isinstance(content, str) else 0,
                "content_hash": (
                    hashlib.sha256(content.encode("utf-8")).hexdigest()
                    if isinstance(content, str) and content
                    else ""
                ),
            },
        )


def _denied(permission_key: str) -> ToolExecutionDecision:
    return ToolExecutionDecision(
        action="deny",
        code=ErrorCode.AUTH_PERMISSION_DENIED,
        message="Permission denied",
        permission_key=permission_key,
    )
