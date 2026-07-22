from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from agent.protocols.session import SessionContext
from agent.session.subagent_preferences import JsonSessionSubagentPreferenceStore


def test_subagent_preferences_preserve_other_sidecar_fields_and_reset_once(tmp_path):
    context = _context(tmp_path)
    context.sessions_meta_dir.mkdir(parents=True)
    path = context.sessions_meta_dir / "alpha.json"
    path.write_text('{"title":"Important","preferred_model_name":"m"}\n', encoding="utf-8")
    store = JsonSessionSubagentPreferenceStore()

    assert store.get(context, "alpha").mode == "auto"
    store.set_mode(context, "alpha", "off")
    store.force_once(context, "alpha")
    store.clear_force_once(context, "alpha")

    assert store.get(context, "alpha").mode == "off"
    assert store.get(context, "alpha").force_once is False
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "preferred_model_name": "m",
        "subagent_mode": "off",
        "title": "Important",
    }


def test_subagent_force_once_is_consumed_atomically(tmp_path):
    context = _context(tmp_path)
    store = JsonSessionSubagentPreferenceStore()
    store.force_once(context, "alpha")

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: store.consume_force_once(context, "alpha"), range(4)))

    assert results.count(True) == 1
    assert results.count(False) == 3


def _context(tmp_path) -> SessionContext:
    return SessionContext(
        owner_user_id=None,
        sessions_dir=tmp_path / "sessions",
        sessions_meta_dir=tmp_path / "sessions_meta",
        files_dir=tmp_path,
        shared_readonly_dir=tmp_path / "shared",
    )
