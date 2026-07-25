"""Deterministic Markdown-to-plain-text rendering for text-only clients."""

from __future__ import annotations

import html
import re

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*([^`]*)$")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_TASK_RE = re.compile(r"^(\s*)[-*+]\s+\[([ xX])\]\s+(.+)$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.+)$")
_ORDERED_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.+)$")
_QUOTE_RE = re.compile(r"^(\s*)(>+)\s?(.*)$")
_HORIZONTAL_RULE_RE = re.compile(r"^\s{0,3}((\*\s*){3,}|(-\s*){3,}|(_\s*){3,})$")
_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")
_AUTOLINK_RE = re.compile(r"<((?:https?://|mailto:)[^>]+)>")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
_ESCAPED_MARKDOWN_RE = re.compile(r"\\([\\`*{}\[\]()#+\-.!_>~|])")


def markdown_to_plain_text(text: str) -> str:
    """Render common Markdown structures without losing their readable content."""

    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not source:
        return ""

    lines = source.split("\n")
    output: list[str] = []
    in_fence = False
    fence_char = ""
    index = 0

    while index < len(lines):
        line = lines[index]
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
                language = fence.group(2).strip()
                if language:
                    output.append(f"[代码：{language}]")
            elif marker[0] == fence_char:
                in_fence = False
                fence_char = ""
            else:
                output.append(line)
            index += 1
            continue

        if in_fence:
            output.append(line)
            index += 1
            continue

        if index + 1 < len(lines) and _is_table_row(line) and _is_table_separator(lines[index + 1]):
            headers = [_render_inline(cell) for cell in _table_cells(line)]
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and _is_table_row(lines[index]):
                rows.append([_render_inline(cell) for cell in _table_cells(lines[index])])
                index += 1
            if rows:
                for row in rows:
                    cells = []
                    for cell_index, value in enumerate(row):
                        if not value:
                            continue
                        header = headers[cell_index] if cell_index < len(headers) else ""
                        cells.append(f"{header}：{value}" if header else value)
                    if cells:
                        output.append("；".join(cells))
            elif any(headers):
                output.append(" | ".join(header for header in headers if header))
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            output.append(_render_inline(heading.group(1)))
            index += 1
            continue

        task = _TASK_RE.match(line)
        if task:
            indent = _plain_indent(task.group(1))
            marker = "☑" if task.group(2).lower() == "x" else "☐"
            output.append(f"{indent}{marker} {_render_inline(task.group(3))}")
            index += 1
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            output.append(f"{_plain_indent(bullet.group(1))}• {_render_inline(bullet.group(2))}")
            index += 1
            continue

        ordered = _ORDERED_RE.match(line)
        if ordered:
            output.append(
                f"{_plain_indent(ordered.group(1))}{ordered.group(2)}. "
                f"{_render_inline(ordered.group(3))}"
            )
            index += 1
            continue

        quote = _QUOTE_RE.match(line)
        if quote:
            prefix = "│ " * len(quote.group(2))
            output.append(f"{_plain_indent(quote.group(1))}{prefix}{_render_inline(quote.group(3))}")
            index += 1
            continue

        if _HORIZONTAL_RULE_RE.match(line):
            output.append("────────")
            index += 1
            continue

        output.append(_render_inline(line))
        index += 1

    return _collapse_blank_lines(output).strip()


def _render_inline(text: str) -> str:
    rendered = str(text)
    rendered = _IMAGE_RE.sub(lambda match: _plain_image(match.group(1), match.group(2)), rendered)
    rendered = _LINK_RE.sub(lambda match: _plain_link(match.group(1), match.group(2)), rendered)
    rendered = _AUTOLINK_RE.sub(lambda match: match.group(1), rendered)
    rendered = _INLINE_CODE_RE.sub(lambda match: match.group(1), rendered)
    rendered = re.sub(r"\*\*\*([^*\n]+)\*\*\*", r"\1", rendered)
    rendered = re.sub(r"___([^_\n]+)___", r"\1", rendered)
    rendered = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", rendered)
    rendered = re.sub(r"__([^_\n]+)__", r"\1", rendered)
    rendered = re.sub(r"~~([^~\n]+)~~", r"\1", rendered)
    rendered = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", rendered)
    rendered = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", rendered)
    rendered = re.sub(r"<br\s*/?>", " ", rendered, flags=re.IGNORECASE)
    rendered = _HTML_TAG_RE.sub("", rendered)
    rendered = _ESCAPED_MARKDOWN_RE.sub(r"\1", rendered)
    return html.unescape(rendered).rstrip()


def _plain_link(label: str, url: str) -> str:
    rendered_label = _render_inline(label).strip()
    return url if not rendered_label or rendered_label == url else f"{rendered_label}：{url}"


def _plain_image(label: str, url: str) -> str:
    rendered_label = _render_inline(label).strip() or "图片"
    return f"[图片：{rendered_label}] {url}"


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return "|" in stripped and bool(_table_cells(stripped))


def _is_table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(_TABLE_SEPARATOR_CELL_RE.fullmatch(cell.replace(" ", "")) for cell in cells)


def _table_cells(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")] if stripped else []


def _plain_indent(indent: str) -> str:
    return "  " * max(0, len(indent.expandtabs(4)) // 4)


def _collapse_blank_lines(lines: list[str]) -> str:
    collapsed: list[str] = []
    blank = False
    for line in lines:
        if line.strip():
            collapsed.append(line.rstrip())
            blank = False
        elif not blank and collapsed:
            collapsed.append("")
            blank = True
    return "\n".join(collapsed)
