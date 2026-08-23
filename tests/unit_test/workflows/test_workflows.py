import json
import time
from pathlib import Path

import pytest

from agent.protocols.auth import ActorContext
from agent.protocols.llm import LLMResponse
from agent.protocols.tool import ToolResult
from agent.workflows.authorization import WorkflowAuthorizationPolicy
from agent.workflows.catalog import WorkflowValidationError, schema_hash, validate_definition
from agent.workflows.executor import WorkflowExecutor, safe_summary
from agent.workflows.nodes import (
    NodeHandlers,
    _parse_tool_output,
    _validated_tool_output,
    resolve_reference,
)
from agent.workflows.runtime import WorkflowRuntime
from agent.workflows.schemas import WorkflowDefinitionV1, WorkflowEdge, WorkflowNode
from agent.workflows.store import WorkflowStore
from agent.workflows.tool_inputs import with_required_query_helpers


@pytest.mark.parametrize("operation", ["run_workflow", "pause_workflow", "resume_workflow", "delete_workflow"])
def test_missing_workflow_api_operations_return_stable_404(monkeypatch, operation):
    from agent.app.api import workflow_routes
    from agent.app.api.routes import ApiError

    class MissingStore:
        def delete(self, *_args):
            raise KeyError("WORKFLOW_NOT_FOUND")

    class MissingService:
        store = MissingStore()

        def run(self, *_args):
            raise KeyError("WORKFLOW_NOT_FOUND")

        def pause(self, *_args):
            raise KeyError("WORKFLOW_NOT_FOUND")

        def resume(self, *_args):
            raise KeyError("WORKFLOW_NOT_FOUND")

    monkeypatch.setattr(workflow_routes, "_service", lambda _request: MissingService())
    monkeypatch.setattr(workflow_routes, "_actor", lambda _request, **_kwargs: object())
    monkeypatch.setattr(workflow_routes, "_owner", lambda _request: "user")

    with pytest.raises(ApiError) as error:
        getattr(workflow_routes, operation)("missing", object())

    assert error.value.code == "WORKFLOW_NOT_FOUND"
    assert error.value.status_code == 404


def test_tool_test_maps_expected_input_failure_to_stable_api_error(monkeypatch):
    from agent.app.api import workflow_routes
    from agent.app.api.routes import ApiError

    class FailingService:
        def test_query_tool(self, *_args):
            raise ValueError("WORKFLOW_LOCATION_NOT_FOUND")

    monkeypatch.setattr(workflow_routes, "_service", lambda _request: FailingService())
    monkeypatch.setattr(workflow_routes, "_actor", lambda _request, **_kwargs: object())
    monkeypatch.setattr(workflow_routes, "_owner", lambda _request: "user")

    with pytest.raises(ApiError) as error:
        workflow_routes.test_workflow_tool(
            {"name": "mcp__open-meteo__get_forecast", "arguments": {"place_name": "重庆南岸区"}},
            object(),
        )

    assert error.value.code == "WORKFLOW_LOCATION_NOT_FOUND"
    assert error.value.status_code == 422


def definition(*, cycle=False):
    nodes = (
        WorkflowNode("trigger", "schedule_trigger"),
        WorkflowNode("condition", "condition", config={"left": 1, "operator": "eq", "right": 1}),
        WorkflowNode("yes", "template", config={"template": "yes"}),
        WorkflowNode("no", "template", config={"template": "no"}),
    )
    edges = [
        WorkflowEdge("a", "trigger", target_node_id="condition"),
        WorkflowEdge("b", "condition", target_node_id="yes", condition_branch="true"),
        WorkflowEdge("c", "condition", target_node_id="no", condition_branch="false"),
    ]
    if cycle:
        edges.append(WorkflowEdge("d", "yes", target_node_id="condition"))
    return WorkflowDefinitionV1("wf", "user", "workflow", nodes, tuple(edges))


def actor():
    return ActorContext("user", "user", "user", "User", frozenset({"viewer"}), frozenset({"workflow.use"}), "workflow")


def actor_with_notifications():
    return ActorContext(
        "user",
        "user",
        "user",
        "User",
        frozenset({"viewer"}),
        frozenset({"workflow.use", "workflow.notify.self", "workflow.email.send"}),
        "workflow",
    )


def test_validation_and_reference_parser():
    assert validate_definition(definition()) == ["trigger", "condition", "no", "yes"]
    with pytest.raises(WorkflowValidationError) as error:
        validate_definition(definition(cycle=True))
    assert error.value.code == "WORKFLOW_GRAPH_CYCLE"
    assert resolve_reference("${nodes.a.output.items[0].name}", {"a": {"items": [{"name": "ok"}]}}) == "ok"
    with pytest.raises(ValueError):
        resolve_reference("${__import__('os')}", {})
    assert _parse_tool_output('{"status":"success"}\n\n{"status":"success"}') == {
        "status": "success"
    }
    with pytest.raises(RuntimeError, match="WORKFLOW_SOURCE_TIMEOUT"):
        _validated_tool_output({"status": "error", "code": "TRAVEL_SOURCE_TIMEOUT"})
    with pytest.raises(RuntimeError, match="WORKFLOW_SOURCE_AUTH_REQUIRED"):
        _validated_tool_output({"status": "error", "code": "TRAVEL_SOURCE_AUTH_REQUIRED"})


def test_store_publish_is_idempotent_and_owner_scoped(tmp_path: Path):
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    store.save_draft(definition())
    published = store.publish(definition())
    assert published.status == "active"
    assert store.get_draft("wf", owner_user_id="user").status == "active"
    assert store.list_definitions("user")[0].status == "active"
    assert store.get_draft("wf", owner_user_id="user").published_at == published.published_at
    assert store.publish(definition()).published_at == published.published_at
    with pytest.raises(PermissionError):
        store.get_draft("wf", owner_user_id="other")


def test_editing_published_workflow_creates_next_draft_version(tmp_path: Path):
    store = WorkflowStore(tmp_path / "workflow-versions.sqlite3")
    original = store.save_draft(definition())
    store.publish(original)

    changed = WorkflowDefinitionV1.from_dict(
        {
            **original.to_dict(),
            "name": "updated workflow",
            "nodes": [
                *original.to_dict()["nodes"],
                {
                    "id": "result",
                    "type": "template",
                    "config": {"template": "done"},
                },
            ],
            "edges": [
                *original.to_dict()["edges"],
                {
                    "id": "result-edge",
                    "source_node_id": "yes",
                    "target_node_id": "result",
                },
            ],
        }
    )
    draft_v2 = store.save_draft(changed, expected_version=1)

    assert draft_v2.version == 2
    assert store.workflow_state("wf", owner_user_id="user") == {
        "active_version": 1,
        "has_unpublished_changes": True,
        "updated_at": draft_v2.updated_at,
    }

    saved_again = store.save_draft(draft_v2, expected_version=2)
    assert saved_again.version == 2

    published_v2 = store.publish(saved_again)
    assert published_v2.version == 2
    assert store.workflow_state("wf", owner_user_id="user") == {
        "active_version": 2,
        "has_unpublished_changes": False,
        "updated_at": published_v2.published_at,
    }


def test_state_detects_legacy_same_version_draft_content_change(tmp_path: Path):
    store = WorkflowStore(tmp_path / "legacy-same-version.sqlite3")
    original = store.save_draft(definition())
    store.publish(original)
    changed = WorkflowDefinitionV1.from_dict(
        {**original.to_dict(), "name": "legacy unsaved content"}
    )
    with store._connect() as db:
        db.execute(
            "UPDATE workflow_definitions SET draft_json=? WHERE id=?",
            (json.dumps(changed.to_dict()), changed.workflow_id),
        )

    assert store.workflow_state("wf", owner_user_id="user")[
        "has_unpublished_changes"
    ] is True


def test_executor_condition_skip_and_events(tmp_path: Path):
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    store.save_draft(definition())
    store.publish(definition())
    handlers = NodeHandlers(actor=actor(), policy=WorkflowAuthorizationPolicy())
    executor = WorkflowExecutor(store, handlers)
    result = executor.execute(definition())
    executor.shutdown()
    assert result["status"] == "partial"
    assert result["node_statuses"]["no"] == "skipped"
    assert result["outputs"]["yes"] == {"text": "yes"}
    run_detail = store.get_run(result["run_id"], "user")
    output_node = next(node for node in run_detail["nodes"] if node["node_id"] == "yes")
    assert output_node["output_summary"] == '{"text": "yes"}'
    assert "safe_output_summary" not in output_node
    assert store.events_after(result["run_id"])[0]["type"] == "workflow.run.started"
    assert "hunter2" not in safe_summary({"password": "hunter2"})


def test_all_user_facing_processing_handlers_are_reachable():
    class FakeLLM:
        def chat(self, *_args, **_kwargs):
            return LLMResponse(content="整理完成")

    sent = []
    handlers = NodeHandlers(
        actor=actor_with_notifications(),
        policy=WorkflowAuthorizationPolicy(),
        llm=FakeLLM(),
        official_email=lambda **values: sent.append(("official", values)) or {"status": "sent"},
        personal_email=lambda **values: sent.append(("personal", values)) or {"status": "sent"},
        qq_notification=lambda **values: sent.append(("qq", values)) or {"status": "accepted"},
    )

    assert handlers.execute(
        WorkflowNode("ai", "llm_transform", config={"instruction": "摘要", "input": "内容"}),
        {},
        {},
        run_id="run",
    ) == {"text": "整理完成"}
    assert handlers.execute(
        WorkflowNode(
            "notify",
            "official_notification",
            config={"subject": "标题", "body": "**天气提醒**\n\n- 带伞\n- 穿薄外套"},
        ),
        {},
        {},
        run_id="run",
    ) == {"status": "sent"}
    assert handlers.execute(
        WorkflowNode(
            "mail",
            "personal_email",
            config={
                "connection_id": "conn",
                "to": "a@example.com",
                "subject": "标题",
                "body": "最高 **34.6℃**，午后有 *雷雨*。",
                "send_consent_at": "2026-08-21T00:00:00Z",
            },
        ),
        {},
        {},
        run_id="run",
    ) == {"status": "sent"}
    assert handlers.execute(
        WorkflowNode(
            "qq",
            "qq_notification",
            config={
                "content": "**今日建议**",
                "source_ref": {"text": "- 带伞\n- 穿薄外套"},
                "send_consent_at": "2026-08-22T00:00:00Z",
            },
        ),
        {},
        {},
        run_id="run",
    ) == {"status": "accepted"}
    assert [kind for kind, _values in sent] == ["official", "personal", "qq"]
    assert sent[0][1]["body"] == "天气提醒\n\n• 带伞\n• 穿薄外套"
    assert sent[1][1]["body"] == "最高 34.6℃，午后有 雷雨。"
    assert sent[2][1]["body"] == "今日建议\n• 带伞\n• 穿薄外套"


def test_send_result_composes_intro_and_upstream_output():
    sent = []
    handlers = NodeHandlers(
        actor=actor_with_notifications(),
        policy=WorkflowAuthorizationPolicy(),
        official_email=lambda **values: sent.append(values) or {"status": "sent"},
    )
    handlers.execute(
        WorkflowNode(
            "notify",
            "official_notification",
            config={
                "subject": "标题",
                "content": "今日结果：",
                "source_ref": "${nodes.summary.output}",
            },
        ),
        {},
        {"summary": {"text": "晴天"}},
        run_id="run",
    )
    assert sent[0]["body"] == "今日结果：\n晴天"
    assert handlers.execute(
        WorkflowNode(
            "result",
            "template",
            config={"content": "今日结果：", "source_ref": "${nodes.summary.output}"},
        ),
        {},
        {"summary": {"text": "晴天"}},
        run_id="run",
    ) == {"text": "今日结果：\n晴天"}


def test_executor_uses_direct_graph_input_instead_of_stale_delivery_reference(
    tmp_path: Path,
):
    class FakeLLM:
        def __init__(self):
            self.inputs = []

        def chat(self, messages, **_kwargs):
            self.inputs.append(messages[-1]["content"])
            return LLMResponse(content="重庆未来两天炎热，午后可能有雷雨，请注意防暑并带伞。")

    sent = []
    item = WorkflowDefinitionV1(
        "readable-mail",
        "user",
        "readable mail",
        (
            WorkflowNode("trigger", "schedule_trigger"),
            WorkflowNode("raw", "template", config={"template": '{"weather":"raw"}'}),
            WorkflowNode(
                "summary",
                "llm_transform",
                config={
                    "instruction": "整理为可读中文",
                    "input": "${nodes.trigger.output}",
                },
            ),
            WorkflowNode(
                "mail",
                "personal_email",
                config={
                    "connection_id": "connection-1",
                    "to": "user@example.com",
                    "subject": "天气",
                    "source_ref": "${nodes.raw.output}",
                    "send_consent_at": "2026-08-22T00:00:00Z",
                },
            ),
        ),
        (
            WorkflowEdge("first", "trigger", target_node_id="raw"),
            WorkflowEdge("second", "raw", target_node_id="summary"),
            WorkflowEdge("third", "summary", target_node_id="mail"),
        ),
    )
    store = WorkflowStore(tmp_path / "readable-output.sqlite3")
    store.save_draft(item)
    store.publish(item)
    llm = FakeLLM()
    executor = WorkflowExecutor(
        store,
        NodeHandlers(
            actor=actor_with_notifications(),
            policy=WorkflowAuthorizationPolicy(),
            llm=llm,
            personal_email=lambda **values: sent.append(values) or {"status": "sent"},
        ),
    )

    result = executor.execute(item)
    executor.shutdown()

    assert result["status"] == "succeeded"
    assert '{"text": "{\\"weather\\":\\"raw\\"}"}' in llm.inputs[0]
    assert sent[0]["body"] == "重庆未来两天炎热，午后可能有雷雨，请注意防暑并带伞。"


def test_publish_rechecks_personal_connection_ownership(tmp_path: Path):
    store = WorkflowStore(tmp_path / "connections-publish.sqlite3")
    item = WorkflowDefinitionV1(
        "mail-workflow",
        "user",
        "mail",
        (
            WorkflowNode("trigger", "schedule_trigger"),
            WorkflowNode("mail", "personal_email", config={"connection_id": "connection-1"}),
        ),
        (WorkflowEdge("edge", "trigger", target_node_id="mail"),),
        required_permissions=("workflow.use", "workflow.email.send"),
    )
    store.save_draft(item)
    checked = []
    policy = WorkflowAuthorizationPolicy()
    executor = WorkflowExecutor(store, NodeHandlers(actor=actor_with_notifications(), policy=policy))
    runtime = WorkflowRuntime(
        store,
        executor,
        policy,
        connection_validator=lambda current_actor, connection_id: checked.append((current_actor.user_id, connection_id)),
    )
    assert runtime.publish(actor_with_notifications(), item).status == "active"
    assert checked == [("user", "connection-1")]
    executor.shutdown()


def test_publish_rechecks_current_qq_binding_and_consent(tmp_path: Path):
    store = WorkflowStore(tmp_path / "qq-publish.sqlite3")
    item = WorkflowDefinitionV1(
        "qq-workflow",
        "user",
        "qq notify",
        (
            WorkflowNode("trigger", "schedule_trigger"),
            WorkflowNode(
                "qq",
                "qq_notification",
                config={"send_consent_at": "2026-08-22T00:00:00Z"},
            ),
        ),
        (WorkflowEdge("edge", "trigger", target_node_id="qq"),),
        required_permissions=("workflow.use", "workflow.notify.self"),
    )
    store.save_draft(item)
    checked = []
    policy = WorkflowAuthorizationPolicy()
    executor = WorkflowExecutor(
        store,
        NodeHandlers(actor=actor_with_notifications(), policy=policy),
    )
    runtime = WorkflowRuntime(
        store,
        executor,
        policy,
        notification_validator=lambda current_actor, channel: checked.append(
            (current_actor.user_id, channel)
        ),
    )

    assert runtime.publish(actor_with_notifications(), item).status == "active"
    assert checked == [("user", "qq")]

    missing_consent = WorkflowDefinitionV1.from_dict(
        {
            **item.to_dict(),
            "workflow_id": "qq-missing-consent",
            "nodes": [
                item.to_dict()["nodes"][0],
                {**item.to_dict()["nodes"][1], "config": {}},
            ],
        }
    )
    store.save_draft(missing_consent)
    with pytest.raises(PermissionError, match="WORKFLOW_TOOL_NEEDS_REVIEW"):
        runtime.publish(actor_with_notifications(), missing_consent)
    executor.shutdown()


def test_qq_timeout_is_outcome_unknown_and_is_not_retried(tmp_path: Path):
    calls = []
    item = WorkflowDefinitionV1(
        "qq-timeout",
        "user",
        "qq timeout",
        (
            WorkflowNode("trigger", "schedule_trigger"),
            WorkflowNode(
                "qq",
                "qq_notification",
                config={"send_consent_at": "2026-08-22T00:00:00Z"},
                timeout_seconds=0.01,
            ),
        ),
        (WorkflowEdge("edge", "trigger", target_node_id="qq"),),
    )
    store = WorkflowStore(tmp_path / "qq-timeout.sqlite3")
    store.save_draft(item)
    store.publish(item)

    def slow_send(**values):
        calls.append(values)
        time.sleep(0.05)
        return {"status": "accepted"}

    executor = WorkflowExecutor(
        store,
        NodeHandlers(
            actor=actor_with_notifications(),
            policy=WorkflowAuthorizationPolicy(),
            qq_notification=slow_send,
        ),
    )
    result = executor.execute(item)
    executor.shutdown()

    assert result["status"] == "failed"
    assert result["error_code"] == "WORKFLOW_ACTION_OUTCOME_UNKNOWN"
    assert len(calls) == 1


def test_tool_catalog_only_returns_live_allowlisted_tools(tmp_path: Path):
    class FakeTools:
        def definitions(self):
            return [
                {"function": {"name": "mcp__weather", "description": "Weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}},
                {"function": {"name": "mcp__not_allowed", "parameters": {"type": "object"}}},
            ]

        def execute(self, name, arguments):
            return ToolResult(f'{{"tool":"{name}","city":"{arguments.get("city", "")}"}}')

    store = WorkflowStore(tmp_path / "catalog.sqlite3")
    policy = WorkflowAuthorizationPolicy(query_tools=frozenset({"mcp__weather", "mcp__missing"}))
    executor = WorkflowExecutor(store, NodeHandlers(actor=actor(), policy=policy, tools=FakeTools()))
    runtime = WorkflowRuntime(store, executor, policy)
    assert [item["name"] for item in runtime.tool_catalog(actor())] == ["mcp__weather"]
    assert runtime.tool_catalog(actor())[0]["parameters"]["properties"]["city"]["type"] == "string"
    assert runtime.test_query_tool(actor(), "mcp__weather", {"city": "上海"}) == {"tool": "mcp__weather", "city": "上海"}
    with pytest.raises(PermissionError, match="WORKFLOW_TOOL_NOT_ALLOWED"):
        runtime.test_query_tool(actor(), "mcp__not_allowed", {})
    executor.shutdown()


def test_weather_workflow_executes_geocoder_before_forecast():
    weather_schema = {
        "type": "object",
        "properties": {
            "latitude": {"type": "number"},
            "longitude": {"type": "number"},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
        },
        "required": ["latitude", "longitude", "start_date", "end_date"],
    }

    class FakeTools:
        def __init__(self):
            self.calls = []

        def definitions(self):
            return [
                {"function": {"name": "mcp__open-meteo__geocode_place", "parameters": {"type": "object"}}},
                {"function": {"name": "mcp__open-meteo__get_forecast", "parameters": weather_schema}},
            ]

        def execute(self, name, arguments):
            self.calls.append((name, arguments))
            if name.endswith("geocode_place"):
                return ToolResult('{"results":[{"latitude":31.23,"longitude":121.47}]}')
            return ToolResult('{"status":"success"}')

    tools = FakeTools()
    policy = WorkflowAuthorizationPolicy(
        query_tools=with_required_query_helpers({"mcp__open-meteo__get_forecast"})
    )
    handlers = NodeHandlers(actor=actor(), policy=policy, tools=tools)
    node = WorkflowNode(
        "weather",
        "mcp_query",
        config={
            "tool_name": "mcp__open-meteo__get_forecast",
            "input_schema_hash": schema_hash(weather_schema),
            "arguments": {
                "place_name": "上海",
                "start_date": "2026-08-22",
                "end_date": "2026-08-23",
            },
        },
    )

    assert handlers.execute(node, {}, {}, run_id="run") == {"status": "success"}
    assert tools.calls[0][0] == "mcp__open-meteo__geocode_place"
    assert tools.calls[1][1]["latitude"] == 31.23
    assert "place_name" not in tools.calls[1][1]
