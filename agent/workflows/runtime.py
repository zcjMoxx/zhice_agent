"""Application-facing workflow service with current actor checks."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import TimeoutError

from agent.protocols.auth import ActorContext
from agent.workflows.authorization import WorkflowAuthorizationPolicy
from agent.workflows.catalog import schema_hash
from agent.workflows.executor import WorkflowExecutor
from agent.workflows.schemas import WorkflowDefinitionV1, WorkflowNode
from agent.workflows.store import WorkflowStore
from agent.workflows.tool_inputs import is_internal_workflow_helper


class WorkflowRuntime:
    def __init__(self, store: WorkflowStore, executor: WorkflowExecutor, policy: WorkflowAuthorizationPolicy, *, executor_factory: Callable[[ActorContext], WorkflowExecutor] | None = None, capability_provider: Callable[[ActorContext], dict] | None = None, connection_validator: Callable[[ActorContext, str], None] | None = None, notification_validator: Callable[[ActorContext, str], None] | None = None):
        self.store, self.executor, self.policy = store, executor, policy
        self.executor_factory = executor_factory
        self.capability_provider = capability_provider
        self.connection_validator = connection_validator
        self.notification_validator = notification_validator
        self.scheduler = None

    def capabilities(self, actor: ActorContext) -> dict:
        if self.capability_provider is None:
            return {}
        return dict(self.capability_provider(actor))

    def save_draft(self, actor: ActorContext, definition: WorkflowDefinitionV1, *, expected_version: int | None = None) -> WorkflowDefinitionV1:
        self.policy.require_owner(actor, definition)
        return self.store.save_draft(definition, expected_version=expected_version)

    def publish(self, actor: ActorContext, definition: WorkflowDefinitionV1) -> WorkflowDefinitionV1:
        self._validate_draft_execution(actor, definition)
        published = self.store.publish(definition)
        trigger = next(node for node in published.nodes if node.type == "schedule_trigger")
        trigger_type = str(trigger.config.get("trigger_type", "manual"))
        if trigger_type in {"date", "interval", "cron"}:
            self.policy.require(actor, "workflow.schedule")
            trigger_values = dict(trigger.config.get("trigger") or trigger.config)
            trigger_values.pop("trigger_type", None)
            self.store.upsert_schedule(
                published.workflow_id,
                trigger_type,
                trigger_values,
                published.timezone,
                misfire_grace_seconds=int(trigger.config.get("misfire_grace_seconds", 900)),
                coalesce=bool(trigger.config.get("coalesce", True)),
            )
            if self.scheduler is not None:
                schedule = next(item for item in self.store.list_active_schedules() if item["workflow_id"] == published.workflow_id)
                self.scheduler.register(schedule)
        return published

    def _validate_draft_execution(self, actor: ActorContext, definition: WorkflowDefinitionV1) -> None:
        self.policy.require_owner(actor, definition)
        for permission in definition.required_permissions:
            self.policy.require(actor, permission)
        catalog = {item["name"]: item for item in self.tool_catalog(actor)}
        for node in definition.nodes:
            if node.type == "personal_email":
                connection_id = str(node.config.get("connection_id") or "")
                if not connection_id or self.connection_validator is None:
                    raise RuntimeError("CONNECTION_PROVIDER_UNSUPPORTED")
                self.connection_validator(actor, connection_id)
            if node.type in {"qq_notification", "weixin_notification"}:
                if not node.config.get("send_consent_at"):
                    raise PermissionError("WORKFLOW_TOOL_NEEDS_REVIEW")
                self.policy.require(actor, "workflow.notify.self")
                if self.notification_validator is None:
                    raise RuntimeError(
                        "WORKFLOW_QQ_CHANNEL_UNAVAILABLE"
                        if node.type == "qq_notification"
                        else "WORKFLOW_WEIXIN_CHANNEL_UNAVAILABLE"
                    )
                self.notification_validator(
                    actor, "qq" if node.type == "qq_notification" else "weixin"
                )
            if node.type not in {"mcp_query", "mcp_action"}:
                continue
            name = str(node.config.get("tool_name", ""))
            tool = catalog.get(name)
            expected_kind = "action" if node.type == "mcp_action" else "query"
            if tool is None or tool["kind"] != expected_kind:
                raise RuntimeError("WORKFLOW_TOOL_NOT_ALLOWED")
            if node.config.get("input_schema_hash") != tool["schema_hash"]:
                raise RuntimeError("WORKFLOW_TOOL_NEEDS_REVIEW")

    def pause(self, actor: ActorContext, workflow_id: str) -> None:
        definition = self.store.get_draft(workflow_id, owner_user_id=actor.user_id)
        if definition is None:
            raise KeyError("WORKFLOW_NOT_FOUND")
        self.policy.require_owner(actor, definition)
        self.store.set_status(workflow_id, str(actor.user_id), "paused")
        if self.scheduler is not None:
            self.scheduler.unregister(workflow_id)

    def resume(self, actor: ActorContext, workflow_id: str) -> None:
        definition = self.store.get_draft(workflow_id, owner_user_id=actor.user_id)
        if definition is None:
            raise KeyError("WORKFLOW_NOT_FOUND")
        self.policy.require_owner(actor, definition)
        self.store.set_status(workflow_id, str(actor.user_id), "active")
        if self.scheduler is not None:
            schedule = self.store.get_schedule(workflow_id)
            if schedule is not None:
                self.store.enable_schedule(workflow_id)
                schedule["enabled"] = True
                self.scheduler.register(schedule)

    def run(self, actor: ActorContext, workflow_id: str) -> dict:
        definition = self.store.get_published(workflow_id, owner_user_id=actor.user_id)
        if definition is None:
            raise KeyError("WORKFLOW_NOT_FOUND")
        self.policy.require_owner(actor, definition)
        for permission in definition.required_permissions:
            self.policy.require(actor, permission)
        executor = self.executor_factory(actor) if self.executor_factory is not None else self.executor
        try:
            return executor.execute(definition)
        finally:
            if executor is not self.executor:
                executor.shutdown()

    def tool_catalog(self, actor: ActorContext) -> list[dict]:
        """Return tools that are both live for this actor and workflow-allowlisted."""
        executor = self.executor_factory(actor) if self.executor_factory is not None else self.executor
        try:
            provider = executor.handlers.tools
            definitions = provider.definitions() if provider is not None else []
            items: list[dict] = []
            for raw in definitions:
                function = raw.get("function", raw)
                name = str(function.get("name", ""))
                if is_internal_workflow_helper(name):
                    continue
                if name in self.policy.query_tools:
                    kind = "query"
                elif name in self.policy.action_tools:
                    kind = "action"
                else:
                    continue
                parameters = function.get("parameters", {})
                items.append({
                    "name": name,
                    "description": str(function.get("description", "")),
                    "kind": kind,
                    "parameters": parameters,
                    "schema_hash": schema_hash(parameters),
                    "available": True,
                })
            return sorted(items, key=lambda item: (item["kind"], item["name"]))
        finally:
            if executor is not self.executor:
                executor.shutdown()

    def run_draft(self, actor: ActorContext, workflow_id: str) -> dict:
        """Execute the latest saved draft once without publishing or changing activation state."""

        definition = self.store.get_draft(workflow_id, owner_user_id=actor.user_id)
        if definition is None:
            raise KeyError("WORKFLOW_NOT_FOUND")
        self._validate_draft_execution(actor, definition)
        executor = self.executor_factory(actor) if self.executor_factory is not None else self.executor
        try:
            return executor.execute(definition)
        finally:
            if executor is not self.executor:
                executor.shutdown()

    def test_query_tool(self, actor: ActorContext, name: str, arguments: dict) -> object:
        """Execute one live allowlisted read-only tool for editor verification."""
        self.policy.authorize_tool(actor, name, action=False)
        catalog = {item["name"]: item for item in self.tool_catalog(actor)}
        tool = catalog.get(name)
        if tool is None or tool["kind"] != "query":
            raise RuntimeError("WORKFLOW_TOOL_NOT_ALLOWED")
        executor = self.executor_factory(actor) if self.executor_factory is not None else self.executor
        timed_out = False
        try:
            node = WorkflowNode(
                id="tool-test",
                type="mcp_query",
                config={
                    "tool_name": name,
                    "input_schema_hash": tool["schema_hash"],
                    "arguments": arguments,
                },
            )
            future = executor.pool.submit(
                executor.handlers.execute,
                node,
                {},
                {},
                run_id="workflow-tool-test",
            )
            try:
                return future.result(timeout=30)
            except TimeoutError as exc:
                timed_out = True
                future.cancel()
                raise RuntimeError("WORKFLOW_SOURCE_TIMEOUT") from exc
        finally:
            if executor is not self.executor:
                executor.shutdown(wait=not timed_out)
