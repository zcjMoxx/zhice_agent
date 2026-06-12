# ContextBuilder unit cases

These tests pin the no-tool chat context contract from
`docs_design/2026-06-10-ZhiCe-Agent-part2-no-tool-chat-design.md`.

- Build one system message from the required prompt files plus runtime metadata.
- Preserve recent user/assistant history in order.
- Truncate oversized history messages with a visible marker.
- Skip historical tool messages during the second-stage no-tool chat path.
- Raise a clear error when a required prompt file is missing.

