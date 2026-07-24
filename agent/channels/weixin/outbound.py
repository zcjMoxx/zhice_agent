"""Plain-text presentation rules for Weixin direct chat."""

from agent.presentation import markdown_to_plain_text


def render_chunks(content: str, limit: int = 4000) -> tuple[str, ...]:
    text = markdown_to_plain_text(content).strip()
    if not text:
        return ()
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = max(
            remaining.rfind("\n\n", 0, limit + 1),
            remaining.rfind("\n", 0, limit + 1),
            remaining.rfind(" ", 0, limit + 1),
        )
        if split_at < max(1, limit // 2):
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return tuple(chunks)
