from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy" / "scripts" / "deployment_smoke.py"
SPEC = importlib.util.spec_from_file_location("zhice_deployment_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)


class FakeWorkflowClient:
    def __init__(self, *, fail_run: bool = False, fail_cleanup: bool = False) -> None:
        self.definition = smoke.definition("deployment-smoke-test", "first")
        self.definition.update(
            {
                "workflow_id": "workflow-smoke",
                "version": 1,
                "status": "draft",
                "timezone": "Asia/Shanghai",
            }
        )
        self.deleted = False
        self.fail_run = fail_run
        self.fail_cleanup = fail_cleanup
        self.paths: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> tuple[int, dict[str, Any]]:
        self.paths.append((method, path))
        if method == "POST" and path == "/api/workflows":
            return 200, dict(self.definition)
        if method == "PUT" and path.endswith("/draft"):
            assert payload is not None
            self.definition = dict(payload)
            return 200, dict(self.definition)
        if method == "POST" and path.endswith("/publish"):
            self.definition["status"] = "active"
            return 200, dict(self.definition)
        if method == "POST" and path.endswith("/run"):
            return 200, {
                "run_id": "run-smoke",
                "status": "failed" if self.fail_run else "succeeded",
            }
        if method == "GET" and path == "/api/workflow-runs/run-smoke":
            marker = self.definition["nodes"][1]["config"]["template"]
            return 200, {
                "run_id": "run-smoke",
                "status": "succeeded",
                "nodes": [
                    {
                        "node_id": "result",
                        "status": "succeeded",
                        "output_summary": json.dumps({"text": marker}),
                    }
                ],
            }
        if method == "GET" and path.endswith("/runs"):
            return 200, {"items": [{"run_id": "run-smoke", "status": "succeeded"}]}
        if method == "DELETE":
            if self.fail_cleanup:
                raise smoke.SmokeError("cleanup failed")
            self.deleted = True
            return 200, {"status": "deleted"}
        if method == "GET" and self.deleted:
            return 404, {"error": {"code": "WORKFLOW_NOT_FOUND"}}
        if method == "GET":
            return 200, dict(self.definition)
        raise AssertionError((method, path, payload))


def test_core_acceptance_covers_crud_publish_run_history_and_cleanup() -> None:
    client = FakeWorkflowClient()

    steps, resource_id = smoke.run_core(client, "release-1")

    assert resource_id == ""
    assert [step["name"] for step in steps] == [
        "create",
        "read",
        "save",
        "publish",
        "execute",
        "history",
        "cleanup",
    ]
    assert all(step["status"] == "passed" for step in steps)
    assert client.deleted


def test_core_acceptance_preserves_original_failure_and_still_cleans() -> None:
    client = FakeWorkflowClient(fail_run=True)

    with pytest.raises(smoke.CoreSmokeError, match="did not succeed") as error:
        smoke.run_core(client, "release-2")

    assert client.deleted
    assert error.value.resource_id == ""
    assert error.value.steps[-1] == {"name": "cleanup", "status": "passed"}


def test_core_acceptance_treats_cleanup_failure_as_core_failure() -> None:
    client = FakeWorkflowClient(fail_cleanup=True)

    with pytest.raises(smoke.CoreSmokeError, match="cleanup failed") as error:
        smoke.run_core(client, "release-3")

    assert error.value.resource_id == "workflow-smoke"
    assert error.value.steps[-1] == {"name": "cleanup", "status": "warning"}


def test_external_checks_are_independent_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def fake_tool(_client: object, name: str, _args: dict[str, Any], _timeout: float) -> None:
        called.append(name)
        if "tavily" in name:
            raise TimeoutError("upstream timeout")

    monkeypatch.setattr(smoke, "test_tool", fake_tool)
    monkeypatch.setattr(smoke, "run_llm", lambda *_args: called.append("llm"))
    monkeypatch.setattr(smoke, "run_smtp", lambda _env: "skipped")

    results = smoke.run_external(
        object(),
        {
            "AMAP_MAPS_API_KEY": "configured",
            "TAVILY_API_KEY": "configured",
            "XHS_READONLY_UPSTREAM_URL": "http://xhs:18060/mcp",
        },
        "release",
    )

    assert [item["name"] for item in results] == [
        "amap",
        "tavily",
        "12306",
        "xhs",
        "llm",
        "smtp",
    ]
    assert next(item for item in results if item["name"] == "tavily")["status"] == "warning"
    assert next(item for item in results if item["name"] == "smtp")["status"] == "skipped"
    assert "llm" in called


def test_external_checks_skip_unconfigured_optional_integrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        smoke,
        "test_tool",
        lambda _client, name, _args, _timeout: called.append(name),
    )
    monkeypatch.setattr(smoke, "run_llm", lambda *_args: called.append("llm"))
    monkeypatch.setattr(smoke, "run_smtp", lambda _env: "skipped")

    results = smoke.run_external(object(), {}, "release")

    assert {item["name"] for item in results if item["status"] == "skipped"} == {
        "amap",
        "tavily",
        "xhs",
        "smtp",
    }
    assert called == ["mcp__12306__get-tickets", "llm"]


def test_llm_external_check_reports_cleanup_failure() -> None:
    with pytest.raises(smoke.SmokeError, match="cleanup failed"):
        smoke.run_llm(FakeWorkflowClient(fail_cleanup=True), "release")


def test_env_parser_and_report_do_not_add_secret_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ZHICE_DEPLOY_SMOKE_USERNAME=deployment-smoke\n"
        "ZHICE_DEPLOY_SMOKE_PASSWORD='strong-secret-value'\n",
        encoding="utf-8",
    )
    values = smoke.read_env(env_path)
    report = {
        "schema_version": 1,
        "image_ref": "registry/zhice-agent@sha256:" + "a" * 64,
        "core": {"status": "passed"},
    }

    path = smoke.write_report(tmp_path / "reports", "release", report)

    assert values["ZHICE_DEPLOY_SMOKE_PASSWORD"] == "strong-secret-value"
    assert "strong-secret-value" not in path.read_text(encoding="utf-8")
    assert path.name.endswith("-release.json")
