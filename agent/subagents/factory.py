"""Create and run independent child AgentLoop instances."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent.core.loop import AgentLoop, CancellationToken
from agent.prompt_loader import PromptLoader
from agent.protocols.activity import RuntimeActivitySink
from agent.protocols.auth import AuditSink
from agent.protocols.hook import HookRuntime
from agent.protocols.llm import ContextBudget, LLMProvider
from agent.protocols.skill import SkillProvider
from agent.protocols.subagent import SubagentProfile, SubagentTask
from agent.protocols.tool import (
    ToolConfirmationBroker,
    ToolExecutionContext,
    ToolExecutionPolicy,
    ToolProvider,
)
from agent.session import JsonlSessionStore
from agent.subagents.context import FilteredSkillProvider, SubagentContextBuilder
from agent.tools.discovery import with_tool_discovery
from agent.tools.filtered import FilteredToolProvider

ChildLLMFactory = Callable[[SubagentProfile], LLMProvider]
ChildToolProviderFactory = Callable[
    [
        Path,
        SubagentProfile,
        ToolExecutionContext,
        Callable[[dict], None] | None,
        "ChildRunIdentity",
        SkillProvider | None,
    ],
    ToolProvider,
]


@dataclass(frozen=True)
class ChildRunIdentity:
    """Coordinator-assigned stable identifiers for one child run."""

    batch_id: str
    task_id: str
    subagent_id: str
    child_session_id: str
    child_turn_id: str


class ChildAgentFactory:
    """Build one isolated AgentLoop graph for each delegated task."""

    def __init__(
        self,
        *,
        prompt_loader: PromptLoader,
        sessions_root: Path,
        parent_tools: ToolProvider,
        llm_factory: ChildLLMFactory,
        context_budget: ContextBudget | None = None,
        tool_provider_factory: ChildToolProviderFactory,
        skills: SkillProvider | None = None,
        tool_policy: ToolExecutionPolicy | None = None,
        confirmation_broker: ToolConfirmationBroker | None = None,
        activity_sink: RuntimeActivitySink | None = None,
        audit_sink: AuditSink | None = None,
        hook_runtime: HookRuntime | None = None,
    ):
        self.prompt_loader = prompt_loader
        self.sessions_root = Path(sessions_root).expanduser().resolve()
        self.parent_tools = parent_tools
        self.parent_visible_names = tuple(_definition_names(parent_tools))
        self.llm_factory = llm_factory
        self.context_budget = context_budget
        self.tool_provider_factory = tool_provider_factory
        self.skills = skills
        self.tool_policy = tool_policy
        self.confirmation_broker = confirmation_broker
        self.activity_sink = activity_sink
        self.audit_sink = audit_sink
        self.hook_runtime = hook_runtime

    def run_child(
        self,
        task: SubagentTask,
        profile: SubagentProfile,
        parent_context: ToolExecutionContext,
        identity: ChildRunIdentity,
        workspace: Path,
        *,
        cancellation_token: CancellationToken,
        on_event: Callable[[dict], None] | None = None,
    ) -> str:
        """Run one child with fresh context, Session, tools, LLM, and Event scope."""

        filtered_skills = (
            FilteredSkillProvider(self.skills, profile.allowed_skills)
            if self.skills is not None
            else None
        )
        context_builder = SubagentContextBuilder(
            self.prompt_loader,
            profile_name=profile.name,
            profile_description=profile.description,
            expected_output=task.expected_output,
            skills=filtered_skills,
            preload_skills=profile.preload_skills,
        )
        child_sessions_dir = self.sessions_root / "_subagents" / parent_context.root_session_id
        child_sessions = JsonlSessionStore(child_sessions_dir)
        child_tools = self.tool_provider_factory(
            workspace,
            profile,
            parent_context,
            on_event,
            identity,
            filtered_skills,
        )
        parent_intersection = FilteredToolProvider(
            child_tools,
            allowed_tools=self.parent_visible_names,
            audit_sink=self.audit_sink,
        )
        effective_tools = FilteredToolProvider(
            parent_intersection,
            allowed_tools=profile.tools,
            denied_tools=profile.denied_tools,
            audit_sink=self.audit_sink,
        )
        effective_tools = with_tool_discovery(effective_tools)
        child_loop = AgentLoop(
            llm=self.llm_factory(profile),
            sessions=child_sessions,
            context_builder=context_builder,
            workspace=workspace,
            tools=effective_tools,
            max_tool_iterations=profile.max_tool_iterations,
            tool_policy=self.tool_policy,
            confirmation_broker=self.confirmation_broker,
            activity_sink=self.activity_sink,
            audit_sink=self.audit_sink,
            hook_runtime=self.hook_runtime,
        )
        return child_loop.run_turn(
            identity.child_session_id,
            task.task,
            turn_id=identity.child_turn_id,
            on_event=on_event,
            cancellation_token=cancellation_token,
            actor=parent_context.actor,
            channel=parent_context.channel,
            request_id=parent_context.request_id,
            parent_turn_id=parent_context.turn_id,
            runtime_event_scope={
                "agent_id": identity.subagent_id,
                "parent_agent_id": "main",
                "root_session_id": parent_context.root_session_id,
                "root_turn_id": parent_context.root_turn_id,
                "parent_session_id": parent_context.session_id,
                "parent_turn_id": parent_context.turn_id,
                "batch_id": identity.batch_id,
                "task_id": task.task_id,
                "depth": 1,
            },
            context_budget=self.context_budget,
        )


def _definition_names(provider: ToolProvider) -> list[str]:
    names: list[str] = []
    for definition in provider.definitions():
        function = definition.get("function") if isinstance(definition, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return names
