"""Minimal command-line entrypoint for the first-stage Zhice-Agent runtime.

The CLI does not call an LLM yet. It verifies configuration, prompt discovery,
and JSONL session persistence by saving each user input as a session message.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from agent.config import load_config
from agent.message import Message
from agent.prompt_loader import PromptLoader
from agent.session import JsonlSessionStore

DEFAULT_PROMPTS = ["identity", "tool_use_policy", "skills_intro"]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the minimal interactive CLI."""

    parser = argparse.ArgumentParser(prog="zcagent")
    parser.add_argument("--session", default="default", help="Session id to append messages to.")
    parser.add_argument("--workspace", default=None, help="Workspace root override.")
    args = parser.parse_args(argv)

    config = load_config(args.workspace)
    config.ensure_dirs()

    prompt_loader = PromptLoader(config.prompts_dir)
    session_store = JsonlSessionStore(config.sessions_dir)
    _load_startup_prompts(prompt_loader)

    print("ZC-Agent")
    print(f"workspace: {config.workspace}")
    print(f"session: {args.session}")

    while True:
        try:
            user_text = input("> ").strip()
        except EOFError:
            print("bye")
            return 0

        if not user_text:
            continue
        if user_text == "/exit":
            print("bye")
            return 0
        if user_text == "/history":
            _print_history(session_store, args.session)
            continue
        if user_text == "/prompts":
            _print_prompts(prompt_loader)
            continue

        session_store.append(args.session, [Message(role="user", content=user_text)])
        print("saved user message.")


def _load_startup_prompts(prompt_loader: PromptLoader) -> None:
    """Fail early when required first-stage prompt files are missing."""

    prompt_loader.load_many(DEFAULT_PROMPTS)


def _print_history(session_store: JsonlSessionStore, session_id: str) -> None:
    """Print recent session messages for local debugging."""

    state = session_store.load(session_id)
    if not state.messages:
        print("(empty history)")
        return
    for message in state.messages[-10:]:
        print(f"{message.role}: {message.content}")


def _print_prompts(prompt_loader: PromptLoader) -> None:
    """Print known prompt files."""

    names = prompt_loader.available_names()
    if not names:
        print("(no prompts)")
        return
    for name in names:
        print(name)


if __name__ == "__main__":
    raise SystemExit(main())
