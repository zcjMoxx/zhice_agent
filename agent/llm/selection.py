"""Call-scoped model selection and provider binding."""

from __future__ import annotations

import fnmatch

from agent.llm import create_llm_provider_chain
from agent.protocols.llm import (
    ContextBudget,
    LLMConfigurationError,
    LLMEndpoint,
    LLMProvider,
    ModelSelection,
    effective_input_token_limit,
)
from agent.protocols.session import SessionModelPreference


class ConfiguredLLMProviderResolver:
    """Validate session preferences and build independent failover providers."""

    def __init__(self, endpoints: list[LLMEndpoint], *, default_endpoint: str | None = None):
        self._endpoints = [endpoint for endpoint in endpoints if endpoint.enabled]
        if not self._endpoints:
            raise LLMConfigurationError("No enabled LLM endpoints are configured.")
        self._by_name = {endpoint.name: endpoint for endpoint in self._endpoints}
        if len(self._by_name) != len(self._endpoints):
            raise LLMConfigurationError("LLM endpoint names must be unique.")
        if default_endpoint and default_endpoint not in self._by_name:
            raise LLMConfigurationError(
                f"LLM endpoint is not configured or enabled: {default_endpoint}"
            )
        self.default_endpoint = default_endpoint or min(
            enumerate(self._endpoints), key=lambda item: (item[1].priority, item[0])
        )[1].name
        self._context_budget = ContextBudget(
            input_token_limit=min(
                effective_input_token_limit(endpoint) for endpoint in self._endpoints
            ),
            endpoint_names=tuple(endpoint.name for endpoint in self._endpoints),
        )

    def resolve(self, preference: SessionModelPreference | None) -> ModelSelection:
        """Resolve a stored preference or return a safe system/fallback selection."""

        default = self._by_name[self.default_endpoint]
        if preference is None:
            return self._selection(default, default.model, source="system")
        endpoint = self._by_name.get(preference.endpoint_name)
        if endpoint is None or not _supports_model(endpoint, preference.model_name):
            return self._selection(
                default,
                default.model,
                source="fallback",
                reason_code="STALE_MODEL_PREFERENCE",
            )
        return self._selection(
            endpoint,
            preference.model_name,
            source="session",
        )

    def bind(self, selection: ModelSelection) -> LLMProvider:
        """Build a new provider facade so one turn cannot mutate another turn."""

        endpoint = self._by_name.get(selection.endpoint_name)
        if endpoint is None or not _supports_model(endpoint, selection.model_name):
            raise LLMConfigurationError("model selection is no longer configured")
        provider = create_llm_provider_chain(
            self._endpoints,
            preferred_endpoint=selection.endpoint_name,
        )
        provider.set_preferred(selection.endpoint_name, selection.model_name)
        return provider

    def select(self, endpoint_name: str, model_name: str | None = None) -> ModelSelection:
        """Validate an explicit endpoint/model request."""

        endpoint = self._by_name.get(endpoint_name.strip())
        if endpoint is None:
            raise ValueError(f"Unknown endpoint: {endpoint_name}")
        selected_model = (model_name or endpoint.model).strip()
        if not _supports_model(endpoint, selected_model):
            raise ValueError(
                f"Endpoint {endpoint.name!r} does not list model {selected_model!r} as supported"
            )
        return self._selection(endpoint, selected_model, source="session")

    def endpoints(self) -> list[LLMEndpoint]:
        """Return enabled endpoint definitions for model list UI."""

        return list(self._endpoints)

    def context_budget(self, selection: ModelSelection | None = None) -> ContextBudget:
        """Return the failover-safe budget for a resolved or future selection."""

        if selection is not None and selection.context_budget is not None:
            return selection.context_budget
        return self._context_budget

    def _selection(
        self,
        endpoint: LLMEndpoint,
        model_name: str,
        *,
        source: str,
        reason_code: str = "",
    ) -> ModelSelection:
        """Build one selection carrying the failover-safe input budget."""

        return ModelSelection(
            endpoint.name,
            model_name,
            source=source,
            reason_code=reason_code,
            context_budget=self._context_budget,
        )


def _supports_model(endpoint: LLMEndpoint, model: str) -> bool:
    if model == endpoint.model:
        return True
    return any(model == pattern or fnmatch.fnmatchcase(model, pattern) for pattern in endpoint.supported_models)
