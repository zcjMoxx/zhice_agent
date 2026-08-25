#!/usr/bin/env python3
"""Transactional cloud acceptance for a newly deployed ZhiCe-Agent image."""

from __future__ import annotations

import argparse
import json
import smtplib
import ssl
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any


class SmokeError(RuntimeError):
    pass


class CoreSmokeError(SmokeError):
    def __init__(self, message: str, steps: list[dict[str, str]], resource_id: str = ""):
        super().__init__(message)
        self.steps = steps
        self.resource_id = resource_id


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise SmokeError(f"invalid runtime env syntax at line {number}")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        expected: tuple[int, ...] = (200,),
        timeout: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        body = None
        headers = {"Accept": "application/json", "User-Agent": "zhice-deployment-smoke"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path, data=body, headers=headers, method=method
        )
        try:
            response = self.opener.open(request, timeout=timeout or self.timeout)
            status = int(response.status)
            raw = response.read(1_048_576)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(1_048_576)
        except (OSError, TimeoutError) as exc:
            raise SmokeError(f"request failed: {method} {path}: {type(exc).__name__}") from exc
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SmokeError(f"invalid JSON response: {method} {path}: HTTP {status}") from exc
        if status not in expected:
            code = ""
            if isinstance(data, dict):
                error = data.get("error")
                if isinstance(error, dict):
                    code = str(error.get("code") or "")
            suffix = f" ({code})" if code else ""
            raise SmokeError(f"unexpected response: {method} {path}: HTTP {status}{suffix}")
        if not isinstance(data, dict):
            raise SmokeError(f"unexpected response object: {method} {path}")
        return status, data


def definition(name: str, marker: str, *, llm: bool = False) -> dict[str, Any]:
    action = {
        "id": "result",
        "type": "llm_transform" if llm else "template",
        "title": "deployment smoke result",
        "position": {"x": 360, "y": 120},
        "config": (
            {"instruction": "Reply with the single word ok.", "input": marker}
            if llm
            else {"template": marker}
        ),
    }
    return {
        "name": name,
        "description": "temporary deployment acceptance resource",
        "schema_version": 1,
        "nodes": [
            {
                "id": "trigger",
                "type": "schedule_trigger",
                "title": "manual trigger",
                "position": {"x": 80, "y": 120},
                "config": {},
            },
            action,
        ],
        "edges": [
            {
                "id": "trigger-result",
                "source_node_id": "trigger",
                "source_port": "output",
                "target_node_id": "result",
                "target_port": "input",
            }
        ],
    }


def clean_definition(item: dict[str, Any], marker: str) -> dict[str, Any]:
    payload = definition(str(item["name"]), marker)
    payload.update(
        {
            "workflow_id": str(item["workflow_id"]),
            "version": int(item.get("version") or 1),
            "status": str(item.get("status") or "draft"),
            "timezone": str(item.get("timezone") or "Asia/Shanghai"),
            "required_permissions": list(item.get("required_permissions") or []),
            "connection_ids": list(item.get("connection_ids") or []),
        }
    )
    return payload


def run_core(client: ApiClient, release_id: str) -> tuple[list[dict[str, str]], str]:
    steps: list[dict[str, str]] = []
    workflow_id = ""
    marker = f"zhice-deployment-smoke-{release_id}"
    updated_marker = marker + "-saved"
    original_error: Exception | None = None
    try:
        _, created = client.request("POST", "/api/workflows", definition(marker, marker))
        workflow_id = str(created.get("workflow_id") or "")
        if not workflow_id:
            raise SmokeError("create workflow did not return workflow_id")
        steps.append({"name": "create", "status": "passed"})

        _, loaded = client.request("GET", f"/api/workflows/{workflow_id}")
        if len(loaded.get("nodes") or []) != 2 or len(loaded.get("edges") or []) != 1:
            raise SmokeError("created workflow graph did not persist")
        steps.append({"name": "read", "status": "passed"})

        update = clean_definition(loaded, updated_marker)
        update["expected_version"] = int(loaded.get("version") or 1)
        client.request("PUT", f"/api/workflows/{workflow_id}/draft", update)
        _, reloaded = client.request("GET", f"/api/workflows/{workflow_id}")
        configs = [node.get("config", {}) for node in reloaded.get("nodes") or []]
        if not any(config.get("template") == updated_marker for config in configs):
            raise SmokeError("updated workflow draft did not persist")
        steps.append({"name": "save", "status": "passed"})

        client.request("POST", f"/api/workflows/{workflow_id}/publish", {})
        steps.append({"name": "publish", "status": "passed"})

        _, executed = client.request("POST", f"/api/workflows/{workflow_id}/run", {})
        if executed.get("status") != "succeeded":
            raise SmokeError("workflow execution did not succeed")
        run_id = str(executed.get("run_id") or "")
        if not run_id:
            raise SmokeError("workflow execution did not return run_id")
        steps.append({"name": "execute", "status": "passed"})

        _, run = client.request("GET", f"/api/workflow-runs/{run_id}")
        nodes = run.get("nodes") or []
        result = next((node for node in nodes if node.get("node_id") == "result"), {})
        if run.get("status") != "succeeded" or result.get("status") != "succeeded":
            raise SmokeError("persisted workflow run is incomplete")
        if updated_marker not in str(result.get("output_summary") or ""):
            raise SmokeError("persisted workflow output did not match marker")
        _, runs = client.request("GET", f"/api/workflows/{workflow_id}/runs")
        if not any(
            str(item.get("run_id") or item.get("id") or "") == run_id
            for item in runs.get("items") or []
        ):
            raise SmokeError("workflow run is absent from history")
        steps.append({"name": "history", "status": "passed"})
    except Exception as exc:  # preserve the original acceptance failure through cleanup
        original_error = exc
    finally:
        if workflow_id:
            try:
                client.request("DELETE", f"/api/workflows/{workflow_id}")
                client.request("GET", f"/api/workflows/{workflow_id}", expected=(404,))
                steps.append({"name": "cleanup", "status": "passed"})
                workflow_id = ""
            except Exception as exc:
                steps.append({"name": "cleanup", "status": "warning"})
                if original_error is None:
                    original_error = exc
    if original_error is not None:
        raise CoreSmokeError(str(original_error), steps, workflow_id) from original_error
    return steps, workflow_id


def test_tool(client: ApiClient, name: str, arguments: dict[str, Any], timeout: float) -> None:
    _, payload = client.request(
        "POST",
        "/api/workflow-tools/test",
        {"name": name, "arguments": arguments},
        timeout=timeout,
    )
    if payload.get("status") != "succeeded":
        raise SmokeError(f"tool check did not succeed: {name}")


def run_llm(client: ApiClient, release_id: str) -> None:
    workflow_id = ""
    original_error: Exception | None = None
    try:
        payload = definition(f"deployment-smoke-llm-{release_id}", f"llm-{release_id}", llm=True)
        _, created = client.request("POST", "/api/workflows", payload)
        workflow_id = str(created.get("workflow_id") or "")
        client.request("POST", f"/api/workflows/{workflow_id}/publish", {})
        _, executed = client.request("POST", f"/api/workflows/{workflow_id}/run", {}, timeout=30)
        if executed.get("status") != "succeeded":
            raise SmokeError("LLM workflow did not succeed")
    except Exception as exc:
        original_error = exc
    finally:
        if workflow_id:
            try:
                client.request("DELETE", f"/api/workflows/{workflow_id}")
            except Exception as exc:
                if original_error is None:
                    original_error = exc
    if original_error is not None:
        raise original_error


def run_smtp(env: dict[str, str]) -> str:
    names = (
        "ZHICE_SMTP_HOST",
        "ZHICE_SMTP_PORT",
        "ZHICE_SMTP_USERNAME",
        "ZHICE_SMTP_PASSWORD",
        "ZHICE_SMTP_FROM",
    )
    configured = [bool(env.get(name, "").strip()) for name in names]
    if not any(configured):
        return "skipped"
    if not all(configured):
        raise SmokeError("SMTP configuration is incomplete")
    host, port = env[names[0]].strip(), int(env[names[1]])
    context = ssl.create_default_context()
    if port == 465:
        client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
    else:
        client = smtplib.SMTP(host, port, timeout=15)
        client.ehlo()
        client.starttls(context=context)
    try:
        client.login(env[names[2]], env[names[3]])
    finally:
        try:
            client.quit()
        except smtplib.SMTPException:
            client.close()
    return "passed"


def run_external(client: ApiClient, env: dict[str, str], release_id: str) -> list[dict[str, str]]:
    tomorrow = (datetime.now().astimezone().date() + timedelta(days=1)).isoformat()
    checks: list[tuple[str, bool, Any]] = [
        (
            "amap",
            bool(env.get("AMAP_MAPS_API_KEY", "").strip()),
            lambda: test_tool(
                client,
                "mcp__amap-maps__maps_text_search",
                {"keywords": "北京市天安门广场", "city": "北京"},
                20,
            ),
        ),
        (
            "tavily",
            bool(env.get("TAVILY_API_KEY", "").strip()),
            lambda: test_tool(
                client,
                "mcp__tavily__tavily_search",
                {"query": "中华人民共和国中央人民政府官网", "max_results": 1},
                20,
            ),
        ),
        (
            "12306",
            True,
            lambda: test_tool(
                client,
                "mcp__12306__get-tickets",
                {"date": tomorrow, "departure_name": "北京南", "arrival_name": "天津南"},
                20,
            ),
        ),
        (
            "xhs",
            bool(env.get("XHS_READONLY_UPSTREAM_URL", "").strip()),
            lambda: test_tool(
                client,
                "mcp__xhs-readonly__search_notes",
                {"keyword": "北京旅行", "max_results": 1},
                30,
            ),
        ),
        ("llm", True, lambda: run_llm(client, release_id)),
        ("smtp", True, lambda: run_smtp(env)),
    ]
    results: list[dict[str, str]] = []
    for name, configured, check in checks:
        if not configured:
            results.append({"name": name, "status": "skipped"})
            continue
        started = time.monotonic()
        try:
            status = check() or "passed"
            results.append(
                {
                    "name": name,
                    "status": status,
                    "duration_ms": str(int((time.monotonic() - started) * 1000)),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "name": name,
                    "status": "warning",
                    "code": type(exc).__name__,
                    "duration_ms": str(int((time.monotonic() - started) * 1000)),
                }
            )
    return results


def write_report(report_dir: Path, release_id: str, report: dict[str, Any]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    safe_release = "".join(
        character for character in release_id if character.isalnum() or character in "-._"
    )[:128]
    path = report_dir / f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{safe_release}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="ZhiCe-Agent cloud deployment acceptance")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--runtime-env", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--image-ref", required=True)
    parser.add_argument("--runtime-backup", default="")
    parser.add_argument("--skip-external", action="store_true")
    args = parser.parse_args()

    release_id = args.image_ref.rsplit("@sha256:", 1)[-1][:12]
    report: dict[str, Any] = {
        "schema_version": 1,
        "image_ref": args.image_ref,
        "release_id": release_id,
        "started_at": utc_now(),
        "runtime_backup": args.runtime_backup,
        "core": {"status": "failed", "steps": []},
        "external": [],
        "rollback_required": True,
    }
    exit_code = 1
    try:
        env = read_env(args.runtime_env)
        username = env.get("ZHICE_DEPLOY_SMOKE_USERNAME", "").strip()
        password = env.get("ZHICE_DEPLOY_SMOKE_PASSWORD", "")
        if not username or not password:
            raise SmokeError("deployment smoke credentials are not configured")
        client = ApiClient(args.base_url)
        client.request("POST", "/api/auth/login", {"username": username, "password": password})
        steps, leaked_workflow = run_core(client, release_id)
        steps.insert(0, {"name": "login", "status": "passed"})
        report["core"] = {"status": "passed", "steps": steps}
        if leaked_workflow:
            report["core"]["cleanup_resource_id"] = leaked_workflow
        if args.skip_external:
            report["external"] = [{"name": "all", "status": "skipped"}]
        else:
            report["external"] = run_external(client, env, release_id)
        report["rollback_required"] = False
        exit_code = 0
    except Exception as exc:
        if isinstance(exc, CoreSmokeError):
            report["core"]["steps"] = exc.steps
            if exc.resource_id:
                report["core"]["cleanup_resource_id"] = exc.resource_id
        report["core"]["code"] = type(exc).__name__
        report["core"]["message"] = str(exc)[:240]
    finally:
        report["finished_at"] = utc_now()
        path = write_report(args.report_dir, release_id, report)
        print(f"Deployment acceptance report: {path}")
        for item in report.get("external") or []:
            print(f"external {item['name']}: {item['status']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
