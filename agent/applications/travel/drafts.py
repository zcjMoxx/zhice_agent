"""Bounded server-owned travel plan draft repair helpers."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

MAX_REPAIR_OPERATIONS = 50
MAX_REPAIR_BYTES = 64 * 1024
MAX_REPAIR_PATH_CHARS = 300
MAX_REPAIR_DEPTH = 10

_EDITABLE_ROOTS = frozenset(
    {
        "assumptions",
        "freshness_summary",
        "transport_options",
        "stay_recommendations",
        "days",
        "budget",
        "weather_summary",
        "fallbacks",
        "avoidance_tips",
        "evidence",
        "unknowns",
        "generated_at",
    }
)


@dataclass(frozen=True)
class TravelDraftRepairError(ValueError):
    """Safe repair error returned through the travel Tool boundary."""

    code: str
    message: str
    field: str = ""

    def __str__(self) -> str:
        return self.message


def travel_plan_draft_revision(plan: dict[str, Any]) -> str:
    """Return a stable optimistic-concurrency revision for one JSON draft."""

    try:
        encoded = json.dumps(
            plan,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TravelDraftRepairError(
            "TRAVEL_PLAN_DRAFT_INVALID",
            "Travel plan draft is not valid JSON.",
            "plan",
        ) from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def apply_travel_plan_repairs(
    plan: dict[str, Any],
    repairs: object,
) -> dict[str, Any]:
    """Apply bounded set/remove operations to an isolated plan copy."""

    if not isinstance(repairs, list) or not 0 <= len(repairs) <= MAX_REPAIR_OPERATIONS:
        raise TravelDraftRepairError(
            "TRAVEL_PLAN_REPAIR_INVALID",
            f"repairs must contain between 0 and {MAX_REPAIR_OPERATIONS} operations.",
            "repairs",
        )
    try:
        encoded = json.dumps(repairs, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TravelDraftRepairError(
            "TRAVEL_PLAN_REPAIR_INVALID",
            "repairs must be valid JSON.",
            "repairs",
        ) from exc
    if len(encoded.encode("utf-8")) > MAX_REPAIR_BYTES:
        raise TravelDraftRepairError(
            "TRAVEL_PLAN_REPAIR_TOO_LARGE",
            "Travel plan repair payload exceeds the size limit.",
            "repairs",
        )

    repaired = deepcopy(plan)
    for index, raw in enumerate(repairs):
        field = f"repairs[{index}]"
        if not isinstance(raw, dict) or set(raw) - {"op", "path", "value"}:
            raise TravelDraftRepairError(
                "TRAVEL_PLAN_REPAIR_INVALID",
                "Each repair must contain only op, path, and optional value.",
                field,
            )
        op = str(raw.get("op") or "").strip().casefold()
        if op not in {"set", "remove"}:
            raise TravelDraftRepairError(
                "TRAVEL_PLAN_REPAIR_INVALID",
                "Repair op must be set or remove.",
                f"{field}.op",
            )
        if op == "set" and "value" not in raw:
            raise TravelDraftRepairError(
                "TRAVEL_PLAN_REPAIR_INVALID",
                "A set repair requires value.",
                f"{field}.value",
            )
        if op == "remove" and "value" in raw:
            raise TravelDraftRepairError(
                "TRAVEL_PLAN_REPAIR_INVALID",
                "A remove repair must not include value.",
                f"{field}.value",
            )
        tokens = _repair_path(raw.get("path"), field=f"{field}.path")
        parent, leaf = _resolve_parent(repaired, tokens, field=f"{field}.path")
        if op == "set":
            _set_value(parent, leaf, deepcopy(raw["value"]), field=f"{field}.path")
        else:
            _remove_value(parent, leaf, field=f"{field}.path")
    return repaired


def _repair_path(value: object, *, field: str) -> list[str]:
    if not isinstance(value, str) or not value.startswith("/"):
        raise TravelDraftRepairError(
            "TRAVEL_PLAN_REPAIR_PATH_INVALID",
            "Repair path must be a non-root JSON Pointer.",
            field,
        )
    if len(value) > MAX_REPAIR_PATH_CHARS:
        raise TravelDraftRepairError(
            "TRAVEL_PLAN_REPAIR_PATH_INVALID",
            "Repair path exceeds the size limit.",
            field,
        )
    raw_tokens = value[1:].split("/")
    if not 1 <= len(raw_tokens) <= MAX_REPAIR_DEPTH:
        raise TravelDraftRepairError(
            "TRAVEL_PLAN_REPAIR_PATH_INVALID",
            "Repair path exceeds the allowed depth.",
            field,
        )
    tokens = [_decode_pointer_token(token, field=field) for token in raw_tokens]
    if not tokens[0] or tokens[0] not in _EDITABLE_ROOTS:
        raise TravelDraftRepairError(
            "TRAVEL_PLAN_REPAIR_PATH_DENIED",
            "Repair path is outside the editable travel-plan fields.",
            field,
        )
    return tokens


def _decode_pointer_token(token: str, *, field: str) -> str:
    decoded: list[str] = []
    index = 0
    while index < len(token):
        if token[index] != "~":
            decoded.append(token[index])
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise TravelDraftRepairError(
                "TRAVEL_PLAN_REPAIR_PATH_INVALID",
                "Repair path contains invalid JSON Pointer escaping.",
                field,
            )
        decoded.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(decoded)


def _resolve_parent(document: Any, tokens: list[str], *, field: str) -> tuple[Any, str]:
    current = document
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                raise TravelDraftRepairError(
                    "TRAVEL_PLAN_REPAIR_PATH_MISSING",
                    "Repair path parent does not exist.",
                    field,
                )
            current = current[token]
            continue
        if isinstance(current, list):
            position = _list_index(token, len(current), append=False, field=field)
            current = current[position]
            continue
        raise TravelDraftRepairError(
            "TRAVEL_PLAN_REPAIR_PATH_MISSING",
            "Repair path traverses a scalar value.",
            field,
        )
    return current, tokens[-1]


def _set_value(parent: Any, leaf: str, value: Any, *, field: str) -> None:
    if isinstance(parent, dict):
        parent[leaf] = value
        return
    if isinstance(parent, list):
        if leaf == "-":
            parent.append(value)
            return
        position = _list_index(leaf, len(parent), append=False, field=field)
        parent[position] = value
        return
    raise TravelDraftRepairError(
        "TRAVEL_PLAN_REPAIR_PATH_MISSING",
        "Repair target parent is not an object or array.",
        field,
    )


def _remove_value(parent: Any, leaf: str, *, field: str) -> None:
    if isinstance(parent, dict):
        if leaf not in parent:
            raise TravelDraftRepairError(
                "TRAVEL_PLAN_REPAIR_PATH_MISSING",
                "Repair target does not exist.",
                field,
            )
        del parent[leaf]
        return
    if isinstance(parent, list):
        position = _list_index(leaf, len(parent), append=False, field=field)
        del parent[position]
        return
    raise TravelDraftRepairError(
        "TRAVEL_PLAN_REPAIR_PATH_MISSING",
        "Repair target parent is not an object or array.",
        field,
    )


def _list_index(token: str, length: int, *, append: bool, field: str) -> int:
    if append and token == "-":
        return length
    if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
        raise TravelDraftRepairError(
            "TRAVEL_PLAN_REPAIR_PATH_INVALID",
            "Array repair path must use a canonical non-negative index.",
            field,
        )
    position = int(token)
    if position >= length:
        raise TravelDraftRepairError(
            "TRAVEL_PLAN_REPAIR_PATH_MISSING",
            "Array repair index is out of bounds.",
            field,
        )
    return position


__all__ = [
    "MAX_REPAIR_BYTES",
    "MAX_REPAIR_OPERATIONS",
    "TravelDraftRepairError",
    "apply_travel_plan_repairs",
    "travel_plan_draft_revision",
]
