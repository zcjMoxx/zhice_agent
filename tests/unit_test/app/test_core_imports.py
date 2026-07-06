import importlib.util
from pathlib import Path


def test_core_imports_use_direct_paths():
    from agent.core.context import ContextBuilder
    from agent.core.loop import AgentLoop

    assert AgentLoop.__name__ == "AgentLoop"
    assert ContextBuilder.__name__ == "ContextBuilder"


def test_gateway_reexport_module_is_removed():
    repo_root = Path(__file__).resolve().parents[3]

    assert not (repo_root / "agent" / "gateway.py").exists()
    assert importlib.util.find_spec("agent.gateway") is None
