"""Source/build contract tests for the Vue RuntimeEvent frontend."""

from __future__ import annotations

import json
from pathlib import Path

FRONTEND = Path("web/frontend")
PACKAGED_STATIC = Path("agent/web/static")


def test_vue_frontend_declares_confirmed_part16_toolchain_and_commands():
    package = json.loads(FRONTEND.joinpath("package.json").read_text(encoding="utf-8"))

    assert {"vue", "vue-router", "pinia", "@lucide/vue"} <= set(package["dependencies"])
    assert {"vite", "typescript", "vitest", "@vue/test-utils"} <= set(
        package["devDependencies"]
    )
    assert set(package["scripts"]) >= {"lint", "typecheck", "test", "build"}


def test_runtime_event_reducer_is_typed_and_has_ordering_and_child_tests():
    reducer = FRONTEND.joinpath("src/runtime-events/reducer.ts").read_text(encoding="utf-8")
    tests = FRONTEND.joinpath("src/runtime-events/reducer.test.ts").read_text(encoding="utf-8")

    assert "sequence <= state.sequence" in reducer
    assert "childTasks" in reducer
    assert "TERMINAL_EVENTS" in reducer
    assert "ignores stale events" in tests
    assert "tracks child sequences independently" in tests


def test_packaged_vue_build_contains_single_spa_entry_and_runtime_assets():
    index = PACKAGED_STATIC.joinpath("index.html").read_text(encoding="utf-8")

    assert '<div id="app"></div>' in index
    assert 'type="module"' in index
    assert "/static/assets/" in index
    assert PACKAGED_STATIC.joinpath("zhice-logo-a.png").is_file()
    assert any(PACKAGED_STATIC.joinpath("assets").glob("*.js"))
    assert any(PACKAGED_STATIC.joinpath("assets").glob("*.css"))


def test_vue_sources_keep_session_channel_and_interaction_compatibility():
    client = FRONTEND.joinpath("src/api/client.ts").read_text(encoding="utf-8")
    websocket = FRONTEND.joinpath("src/websocket/client.ts").read_text(encoding="utf-8")
    chat = FRONTEND.joinpath("src/stores/chat.ts").read_text(encoding="utf-8")

    assert "/api/channels/qq/link-code" in client
    assert "/api/channels/weixin/reconnect" in client
    assert "/fork" in client
    assert 'type: "hello", client: "web"' in websocket
    assert 'type: "stop"' in websocket
    assert "mcp_elicitation_response" in websocket
    assert "tool_confirmation_required" in chat
