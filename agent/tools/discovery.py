"""Turn-scoped on-demand Tool discovery and schema activation."""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from threading import Lock
from typing import Any

from agent.protocols.tool import ToolExecutionContext, ToolProvider, ToolResult

DISCOVER_TOOLS_NAME = "discover_tools"
DEFAULT_DISCOVERY_RESULTS = 5
MAX_DISCOVERY_RESULTS = 8
MAX_DISCOVERY_QUERY_CHARS = 500
MAX_CATALOG_DESCRIPTION_CHARS = 240

_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:\-]+")
_CJK_CHUNK_RE = re.compile(r"[\u4e00-\u9fff]+")


class DiscoverableToolProvider:
    """Expose one bootstrap Tool, then only schemas activated during this Turn."""

    def __init__(self, base: ToolProvider, *, initial_names: tuple[str, ...] = ()):
        self._base = base
        self._definitions = tuple(copy.deepcopy(base.definitions()))
        self._by_name = {
            name: definition
            for definition in self._definitions
            if (name := _definition_name(definition))
        }
        if DISCOVER_TOOLS_NAME in self._by_name:
            raise ValueError(f"base ToolProvider cannot define reserved tool: {DISCOVER_TOOLS_NAME}")
        self._activated: set[str] = {
            name for name in initial_names if name in self._by_name
        }
        self._lock = Lock()

    @property
    def available_tool_names(self) -> tuple[str, ...]:
        """Return actor-filtered catalog names in stable provider order."""

        return tuple(self._by_name)

    @property
    def activated_tool_names(self) -> tuple[str, ...]:
        """Return activated names in stable provider order."""

        with self._lock:
            return tuple(name for name in self._by_name if name in self._activated)

    def definitions(self) -> list[dict[str, Any]]:
        """Return bootstrap discovery plus currently activated Tool schemas."""

        with self._lock:
            active = set(self._activated)
        all_activated = bool(self._by_name) and active.issuperset(self._by_name)
        definitions = [] if all_activated else [_discovery_definition()]
        definitions.extend(
            copy.deepcopy(definition)
            for name, definition in self._by_name.items()
            if name in active
        )
        return definitions

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Discover/activate safely or dispatch one previously activated Tool."""

        if name == DISCOVER_TOOLS_NAME:
            return self._discover(args)
        denied = self._activation_error(name)
        if denied is not None:
            return denied
        return self._base.execute(name, args)

    def execute_with_context(
        self,
        name: str,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Preserve trusted context when dispatching an activated base Tool."""

        if name == DISCOVER_TOOLS_NAME:
            return self._discover(args)
        denied = self._activation_error(name)
        if denied is not None:
            return denied
        contextual = getattr(self._base, "execute_with_context", None)
        if callable(contextual):
            return contextual(name, args, context)
        return self._base.execute(name, args)

    def _discover(self, args: dict[str, Any]) -> ToolResult:
        if not isinstance(args, dict):
            return _error("Tool discovery arguments must be an object.", "INVALID_PARAM")
        query = args.get("query", "")
        names = args.get("names", [])
        max_results = args.get("max_results", DEFAULT_DISCOVERY_RESULTS)
        if not isinstance(query, str) or len(query) > MAX_DISCOVERY_QUERY_CHARS:
            return _error("Tool discovery query is invalid.", "INVALID_PARAM")
        if not isinstance(names, list) or len(names) > MAX_DISCOVERY_RESULTS or any(
            not isinstance(name, str) or not name for name in names
        ):
            return _error("Tool discovery names are invalid.", "INVALID_PARAM")
        if (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or max_results < 1
            or max_results > MAX_DISCOVERY_RESULTS
        ):
            return _error("Tool discovery max_results is invalid.", "INVALID_PARAM")
        selected = self._select(query.strip(), names, max_results)
        with self._lock:
            self._activated.update(selected)
            activated_names = tuple(name for name in self._by_name if name in self._activated)
        payload = {
            "status": "activated" if selected else "no_match",
            "query": query.strip(),
            "activated": [self._catalog_item(name) for name in selected],
            "activated_names": list(activated_names),
            "available_count": len(self._by_name),
            "hint": (
                "Call an activated tool using its schema on the next model step."
                if selected
                else "Refine the query or request exact tool names."
            ),
        }
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "code": "OK" if selected else "TOOL_DISCOVERY_NO_MATCH",
                "activated_count": len(selected),
                "tool_name": DISCOVER_TOOLS_NAME,
            },
        )

    def _select(self, query: str, names: list[str], max_results: int) -> tuple[str, ...]:
        exact = [name for name in names if name in self._by_name]
        if exact:
            return tuple(dict.fromkeys(exact))[:max_results]
        scored = []
        query_features = _features(query)
        for index, (name, definition) in enumerate(self._by_name.items()):
            description = _definition_description(definition)
            score = _match_score(query, query_features, name, description)
            if score > 0:
                scored.append((score, -index, name))
        if scored:
            scored.sort(reverse=True)
            return tuple(item[2] for item in scored[:max_results])
        return tuple(list(self._by_name)[:max_results]) if query else ()

    def _catalog_item(self, name: str) -> dict[str, str]:
        description = " ".join(_definition_description(self._by_name[name]).split())
        if len(description) > MAX_CATALOG_DESCRIPTION_CHARS:
            description = description[:MAX_CATALOG_DESCRIPTION_CHARS] + "[truncated]"
        return {"name": name, "description": description}

    def _activation_error(self, name: str) -> ToolResult | None:
        with self._lock:
            activated = name in self._activated
        if activated:
            return None
        if name not in self._by_name:
            return _error(f"Unknown tool: {name}", "UNKNOWN_TOOL", tool_name=name)
        return _error(
            f"Tool is not activated for this turn: {name}. Call discover_tools first.",
            "TOOL_NOT_ACTIVATED",
            tool_name=name,
        )


def with_tool_discovery(
    provider: ToolProvider | None,
    *,
    initial_names: tuple[str, ...] = (),
) -> ToolProvider | None:
    """Wrap one final effective Provider once; preserve None for no-tool runtimes."""

    if provider is None or isinstance(provider, DiscoverableToolProvider):
        return provider
    return DiscoverableToolProvider(provider, initial_names=initial_names)


def _discovery_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": DISCOVER_TOOLS_NAME,
            "description": (
                "Discover and activate the smallest set of tools needed for the current user "
                "request. Call this before any other tool. Use concise capability keywords; "
                "after it returns, call only tools whose schemas appear on the next model step."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 500},
                    "names": {
                        "type": "array",
                        "maxItems": MAX_DISCOVERY_RESULTS,
                        "items": {"type": "string", "minLength": 1, "maxLength": 128},
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_DISCOVERY_RESULTS,
                        "default": DEFAULT_DISCOVERY_RESULTS,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }


def _definition_name(definition: Any) -> str:
    function = definition.get("function") if isinstance(definition, dict) else None
    name = function.get("name") if isinstance(function, dict) else None
    return name if isinstance(name, str) and name else ""


def _definition_description(definition: Any) -> str:
    function = definition.get("function") if isinstance(definition, dict) else None
    description = function.get("description") if isinstance(function, dict) else None
    return description if isinstance(description, str) else ""


def _match_score(query: str, query_features: Counter[str], name: str, description: str) -> float:
    if not query_features:
        return 0.0
    text = f"{name} {description}".casefold()
    target = _features(text)
    overlap = sum(weight for feature, weight in query_features.items() if feature in target)
    normalized_name = name.casefold()
    if normalized_name in query.casefold() or query.casefold() in normalized_name:
        overlap += 4.0
    return overlap


def _features(text: str) -> Counter[str]:
    features: Counter[str] = Counter()
    for match in _ASCII_TOKEN_RE.finditer(text):
        token = match.group(0).casefold().strip("._-:/\\")
        if not token:
            continue
        features[token] += 2.0 if any(char in token for char in "_./:-\\") else 1.0
        for part in re.split(r"[._/\-:\\]+", token):
            if part and part != token:
                features[part] += 1.0
    for match in _CJK_CHUNK_RE.finditer(text):
        chunk = match.group(0)
        if len(chunk) == 1:
            features[chunk] += 0.5
        else:
            for index in range(len(chunk) - 1):
                features[chunk[index : index + 2]] += 1.0
    return features


def _error(output: str, code: str, *, tool_name: str = DISCOVER_TOOLS_NAME) -> ToolResult:
    return ToolResult(
        output=output,
        is_error=True,
        metadata={"code": code, "tool_name": tool_name},
    )
