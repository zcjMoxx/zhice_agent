"""Stable DAG executor independent from AgentLoop and chat Session."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from typing import Any
from uuid import uuid4

from agent.workflows.catalog import validate_definition
from agent.workflows.nodes import NodeHandlers
from agent.workflows.schemas import WorkflowDefinitionV1, WorkflowRun
from agent.workflows.store import WorkflowStore

_SECRET = re.compile(r"(?i)(api[_-]?key|authorization|credential|password|secret|token)(\s*[:=]\s*)[^\s,}\"]+")
_SECRET_KEY = re.compile(r"(?i)(api[_-]?key|authorization|credential|password|secret|token)")


def safe_summary(value: Any, limit: int = 4096) -> str:
    def redact(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: "[REDACTED]" if _SECRET_KEY.search(str(key)) else redact(child) for key, child in item.items()}
        if isinstance(item, list):
            return [redact(child) for child in item]
        return item

    try:
        text = json.dumps(redact(value), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return _SECRET.sub(r"\1\2[REDACTED]", text)[:limit]


class WorkflowExecutor:
    def __init__(self, store: WorkflowStore, handlers: NodeHandlers, *, max_workers: int = 4):
        self.store, self.handlers = store, handlers
        self.pool = ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="workflow")
        self._cancelled: dict[str, Event] = {}

    def cancel(self, run_id: str) -> None:
        self._cancelled.setdefault(run_id, Event()).set()

    def execute(self, definition: WorkflowDefinitionV1, *, trigger_type: str = "manual", scheduled_for: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        order = validate_definition(definition)
        run_id = run_id or str(uuid4())
        cancellation = self._cancelled.setdefault(run_id, Event())
        self.store.create_run(WorkflowRun(run_id, definition.workflow_id, definition.version, definition.owner_user_id, trigger_type, scheduled_for=scheduled_for))
        self.store.update_run(run_id, "running")
        self.store.append_event(run_id, "workflow.run.started", {"workflow_id": definition.workflow_id, "version": definition.version})
        nodes = {node.id: node for node in definition.nodes}
        predecessors: dict[str, list[Any]] = {node_id: [] for node_id in nodes}
        for edge in definition.edges:
            predecessors[edge.target_node_id].append(edge)
        outputs: dict[str, Any] = {}
        statuses: dict[str, str] = {}
        try:
            for node_id in order:
                node = nodes[node_id]
                if cancellation.is_set():
                    raise InterruptedError("WORKFLOW_CANCELLED")
                skip = False
                for edge in predecessors[node_id]:
                    if statuses.get(edge.source_node_id) in {"failed", "skipped"}:
                        skip = True
                    source = nodes[edge.source_node_id]
                    if source.type == "condition":
                        result = bool(outputs.get(source.id, {}).get("result"))
                        if edge.condition_branch != str(result).lower():
                            skip = True
                if skip:
                    statuses[node_id] = "skipped"
                    self.store.record_node_run(run_id, node.id, node.type, "skipped")
                    self.store.append_event(run_id, "workflow.node.skipped", {"node_id": node.id})
                    continue
                self.store.append_event(run_id, "workflow.node.started", {"node_id": node.id, "node_type": node.type})
                inputs = dict(node.input_bindings)
                direct_edges = predecessors[node_id]
                if len(direct_edges) == 1:
                    source_output = outputs.get(direct_edges[0].source_node_id)
                    if node.type == "llm_transform":
                        inputs["input"] = source_output
                    if node.type in {"official_notification", "personal_email", "qq_notification"} or (
                        node.type == "template"
                        and ("content" in node.config or "source_ref" in node.config)
                    ):
                        inputs["source_ref"] = source_output
                attempts = node.retry_policy.max_attempts if node.type == "mcp_query" else 1
                for attempt in range(1, attempts + 1):
                    future = self.pool.submit(self.handlers.execute, node, inputs, outputs, run_id=run_id)
                    try:
                        result = future.result(timeout=node.timeout_seconds)
                        outputs[node.id] = result
                        statuses[node.id] = "succeeded"
                        self.store.record_node_run(run_id, node.id, node.type, "succeeded", attempt=attempt, input_summary=safe_summary(inputs), output_summary=safe_summary(result))
                        self.store.append_event(run_id, "workflow.node.completed", {"node_id": node.id})
                        break
                    except TimeoutError as exc:
                        future.cancel()
                        code = "WORKFLOW_ACTION_OUTCOME_UNKNOWN" if node.type in {"mcp_action", "official_notification", "personal_email", "qq_notification"} else "WORKFLOW_NODE_TIMEOUT"
                        raise RuntimeError(code) from exc
                    except Exception:
                        if attempt >= attempts:
                            raise
            final = "partial" if "skipped" in statuses.values() else "succeeded"
            self.store.update_run(run_id, final)
            self.store.append_event(run_id, "workflow.run.completed", {"status": final})
            return {"run_id": run_id, "status": final, "outputs": outputs, "node_statuses": statuses}
        except InterruptedError:
            self.store.update_run(run_id, "cancelled")
            self.store.append_event(run_id, "workflow.run.cancelled")
            return {"run_id": run_id, "status": "cancelled", "outputs": outputs, "node_statuses": statuses}
        except Exception as exc:
            code = str(exc) if str(exc).startswith(("WORKFLOW_", "CONNECTION_", "EMAIL_", "OFFICIAL_", "NOTIFICATION_")) else "WORKFLOW_NODE_FAILED"
            current = next((item for item in order if item not in statuses), "")
            if current:
                node = nodes[current]
                statuses[current] = "failed"
                self.store.record_node_run(run_id, current, node.type, "failed", error_code=code)
                self.store.append_event(run_id, "workflow.node.failed", {"node_id": current, "error_code": code})
            self.store.update_run(run_id, "failed", error_code=code)
            self.store.append_event(run_id, "workflow.run.failed", {"error_code": code})
            return {"run_id": run_id, "status": "failed", "error_code": code, "outputs": outputs, "node_statuses": statuses}
        finally:
            self._cancelled.pop(run_id, None)

    def shutdown(self, wait: bool = True) -> None:
        for event in self._cancelled.values():
            event.set()
        self.pool.shutdown(wait=wait, cancel_futures=True)
