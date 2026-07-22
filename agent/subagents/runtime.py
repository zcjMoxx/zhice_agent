"""Turn-scoped assembly for the parent delegation Tool."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent.core.loop import CancellationToken
from agent.llm.selection import ConfiguredLLMProviderResolver
from agent.protocols.activity import RuntimeActivitySink
from agent.protocols.auth import AuditSink
from agent.protocols.capability import CapabilityStatus
from agent.protocols.hook import HookRuntime
from agent.protocols.llm import ContextBudget, LLMProvider, ModelSelection
from agent.protocols.skill import SkillProvider
from agent.protocols.subagent import SubagentProfile
from agent.protocols.tool import (
    ToolConfirmationBroker,
    ToolExecutionPolicy,
    ToolProvider,
)
from agent.subagents.config import SubagentConfig
from agent.subagents.coordinator import BoundedSubagentCoordinator
from agent.subagents.factory import ChildAgentFactory, ChildToolProviderFactory
from agent.subagents.workspace import shared_workspace_manager
from agent.tools.subagent import (
    AugmentedToolProvider,
    DelegateTasksTool,
    UnavailableDelegateTasksTool,
)


def build_unavailable_subagent_provider(
    base_tools: ToolProvider,
    status: CapabilityStatus,
) -> ToolProvider:
    """Add a non-executing facade that reports the precise startup capability cause."""

    return AugmentedToolProvider(base_tools, (UnavailableDelegateTasksTool(status),))


def build_turn_subagent_provider(
    *,
    base_tools: ToolProvider,
    config: SubagentConfig,
    prompt_loader,
    sessions_root: Path,
    workspace: Path,
    parent_llm: LLMProvider,
    context_budget: ContextBudget | None = None,
    tool_provider_factory: ChildToolProviderFactory,
    skills: SkillProvider | None,
    cancellation_token: CancellationToken,
    on_event=None,
    force_once: bool = False,
    llm_factory: Callable[[SubagentProfile], LLMProvider] | None = None,
    tool_policy: ToolExecutionPolicy | None = None,
    confirmation_broker: ToolConfirmationBroker | None = None,
    activity_sink: RuntimeActivitySink | None = None,
    audit_sink: AuditSink | None = None,
    hook_runtime: HookRuntime | None = None,
) -> ToolProvider:
    """Add one batch Tool whose Coordinator and limits live only for this turn."""

    if not config.enabled:
        return base_tools
    resolved_llm_factory = llm_factory or _configured_child_llm_factory(parent_llm)
    child_factory = ChildAgentFactory(
        prompt_loader=prompt_loader,
        sessions_root=sessions_root,
        parent_tools=base_tools,
        llm_factory=resolved_llm_factory,
        context_budget=context_budget,
        tool_provider_factory=tool_provider_factory,
        skills=skills,
        tool_policy=tool_policy,
        confirmation_broker=confirmation_broker,
        activity_sink=activity_sink,
        audit_sink=audit_sink,
        hook_runtime=hook_runtime,
    )
    coordinator = BoundedSubagentCoordinator(
        config=config,
        child_factory=child_factory,
        workspace_manager=shared_workspace_manager(workspace),
        parent_cancellation_token=cancellation_token,
        activity_sink=activity_sink,
        audit_sink=audit_sink,
        on_event=on_event,
    )
    profiles = tuple(
        (profile.name, profile.description)
        for profile in config.list_profiles()
        if profile.allow_model_invocation
    )
    return AugmentedToolProvider(
        base_tools,
        (DelegateTasksTool(coordinator, profile_summaries=profiles, force_once=force_once),),
    )


def _configured_child_llm_factory(
    parent_llm: LLMProvider,
) -> Callable[[SubagentProfile], LLMProvider]:
    endpoints_method = getattr(parent_llm, "endpoints", None)
    if not callable(endpoints_method):
        raise ValueError("Subagent runtime requires an independent child LLM factory")
    endpoints = list(endpoints_method())
    current_method = getattr(parent_llm, "current_endpoint", None)
    inherited = current_method() if callable(current_method) else endpoints[0]
    resolver = ConfiguredLLMProviderResolver(endpoints, default_endpoint=inherited.name)

    def create(profile: SubagentProfile) -> LLMProvider:
        selected = inherited
        if profile.model_role != "inherit":
            selected = next(
                (endpoint for endpoint in endpoints if endpoint.role == profile.model_role),
                inherited,
            )
        return resolver.bind(ModelSelection(selected.name, selected.model, source="subagent_profile"))

    return create
