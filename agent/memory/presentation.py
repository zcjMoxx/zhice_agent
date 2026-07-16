"""Human-readable presentation helpers for durable Memory commands."""

from __future__ import annotations

from agent.protocols.memory import MEMORY_CATEGORIES, MemoryEntry, MemoryStore


def format_memory_list(store: MemoryStore) -> str:
    """Render the current scoped Memory without exposing storage details."""

    total = store.count()
    if total == 0:
        return "Memory is empty."
    entries = store.search(limit=20)
    grouped: dict[str, list[MemoryEntry]] = {category: [] for category in MEMORY_CATEGORIES}
    for entry in entries:
        grouped[entry.category].append(entry)
    lines = ["Memory:"]
    for category in MEMORY_CATEGORIES:
        values = grouped[category]
        if not values:
            continue
        lines.append(f"\n{category}:")
        lines.extend(f"- {entry.content}" for entry in values)
    if len(entries) < total:
        lines.append(f"\nShowing {len(entries)} of {total} entries.")
    return "\n".join(lines)
