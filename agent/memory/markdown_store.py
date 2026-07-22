"""Human-readable Markdown implementation of durable Memory."""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

from agent.memory import MemoryStoreError
from agent.protocols.memory import MEMORY_CATEGORIES, MemoryContext, MemoryEntry

START_MARKER = "<!-- zhice-memory:start -->"
END_MARKER = "<!-- zhice-memory:end -->"
DEFAULT_MAX_ENTRIES = 200
DEFAULT_MAX_ENTRY_CHARS = 1000
DEFAULT_MAX_FILE_BYTES = 128 * 1024
DEFAULT_MAX_READ_ENTRIES = 20
DEFAULT_MAX_READ_CHARS = 12000
DEFAULT_MAX_QUERY_CHARS = 500

_ENTRY_RE = re.compile(r"^- (.+)$")
_LOCKS: defaultdict[str, threading.RLock] = defaultdict(threading.RLock)


class MarkdownMemoryStore:
    """Persist bounded Memory entries and session summaries as Markdown."""

    def __init__(
        self,
        context: MemoryContext,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_entry_chars: int = DEFAULT_MAX_ENTRY_CHARS,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_read_entries: int = DEFAULT_MAX_READ_ENTRIES,
        max_read_chars: int = DEFAULT_MAX_READ_CHARS,
        max_query_chars: int = DEFAULT_MAX_QUERY_CHARS,
    ):
        self.context = context
        self.max_entries = max_entries
        self.max_entry_chars = max_entry_chars
        self.max_file_bytes = max_file_bytes
        self.max_read_entries = max_read_entries
        self.max_read_chars = max_read_chars
        self.max_query_chars = max_query_chars

    def search(
        self,
        query: str = "",
        *,
        category: str = "",
        offset: int = 0,
        limit: int = 8,
    ) -> list[MemoryEntry]:
        self._validate_category(category, allow_empty=True)
        if not isinstance(query, str) or len(query) > self.max_query_chars:
            raise MemoryStoreError("MEMORY_LIMIT_EXCEEDED", "Memory query is too long.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise MemoryStoreError("MEMORY_LIMIT_EXCEEDED", "Memory result offset is invalid.")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise MemoryStoreError("MEMORY_LIMIT_EXCEEDED", "Memory result limit is invalid.")
        limit = min(limit, self.max_read_entries)
        filtered = self._matching_entries(query, category=category)
        result: list[MemoryEntry] = []
        chars = 0
        for entry in filtered[offset:]:
            if len(result) >= limit or chars + len(entry.content) > self.max_read_chars:
                break
            result.append(entry)
            chars += len(entry.content)
        return result

    def count(self, query: str = "", *, category: str = "") -> int:
        """Return the number of durable entries matching one bounded query."""

        self._validate_category(category, allow_empty=True)
        if not isinstance(query, str) or len(query) > self.max_query_chars:
            raise MemoryStoreError("MEMORY_LIMIT_EXCEEDED", "Memory query is too long.")
        return len(self._matching_entries(query, category=category))

    def _matching_entries(self, query: str, *, category: str) -> list[MemoryEntry]:
        _, _, entries = self._load()
        filtered = [entry for entry in entries if not category or entry.category == category]
        normalized_query = _normalize(query)
        if normalized_query:
            scored = [(_score(normalized_query, entry.content), entry) for entry in filtered]
            filtered = [entry for score, entry in scored if score > 0]
            filtered.sort(key=lambda entry: -_score(normalized_query, entry.content))
            return filtered
        return filtered

    def add(
        self,
        category: str,
        content: str,
    ) -> MemoryEntry:
        self._validate_category(category)
        content = self._validate_content(content)
        with self._lock():
            prefix, suffix, entries = self._load()
            duplicate = next(
                (
                    entry
                    for entry in entries
                    if entry.category == category and _normalize(entry.content) == _normalize(content)
                ),
                None,
            )
            if duplicate is not None:
                return duplicate
            if len(entries) >= self.max_entries:
                raise MemoryStoreError("MEMORY_LIMIT_EXCEEDED", "Memory entry limit reached.")
            entry = MemoryEntry(
                category=category,
                content=content,
            )
            entries.append(entry)
            self._write(prefix, suffix, entries)
            return entry

    def replace(
        self,
        category: str,
        old_content: str,
        content: str,
    ) -> MemoryEntry:
        self._validate_category(category)
        old_content = self._validate_content(old_content)
        content = self._validate_content(content)
        with self._lock():
            prefix, suffix, entries = self._load()
            target_index = next(
                (
                    index
                    for index, entry in enumerate(entries)
                    if entry.category == category
                    and _normalize(entry.content) == _normalize(old_content)
                ),
                None,
            )
            if target_index is None:
                raise MemoryStoreError("MEMORY_ENTRY_NOT_FOUND", "Memory entry was not found.")
            duplicate = next(
                (
                    entry
                    for index, entry in enumerate(entries)
                    if index != target_index
                    and entry.category == category
                    and _normalize(entry.content) == _normalize(content)
                ),
                None,
            )
            if duplicate is not None:
                entries.pop(target_index)
                self._write(prefix, suffix, entries)
                return duplicate
            replacement = MemoryEntry(category=category, content=content)
            entries[target_index] = replacement
            self._write(prefix, suffix, entries)
            return replacement

    def delete(self, category: str, content: str) -> bool:
        self._validate_category(category)
        content = self._validate_content(content)
        with self._lock():
            prefix, suffix, entries = self._load()
            remaining = [
                entry
                for entry in entries
                if not (
                    entry.category == category
                    and _normalize(entry.content) == _normalize(content)
                )
            ]
            if len(remaining) == len(entries):
                raise MemoryStoreError("MEMORY_ENTRY_NOT_FOUND", "Memory entry was not found.")
            self._write(prefix, suffix, remaining)
            return True

    def _load(self) -> tuple[str, str, list[MemoryEntry]]:
        path = self.context.durable_file
        if not path.exists():
            text = _default_template()
            _atomic_write(path, text)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MemoryStoreError("MEMORY_NOT_CONFIGURED", "Cannot read Memory file.") from exc
        if len(text.encode("utf-8")) > self.max_file_bytes:
            raise MemoryStoreError("MEMORY_LIMIT_EXCEEDED", "Memory file is too large.")
        if text.count(START_MARKER) != 1 or text.count(END_MARKER) != 1:
            raise MemoryStoreError("MEMORY_FORMAT_INVALID", "Memory management markers are invalid.")
        start = text.index(START_MARKER)
        end = text.index(END_MARKER)
        if end <= start:
            raise MemoryStoreError("MEMORY_FORMAT_INVALID", "Memory management markers are invalid.")
        prefix = text[:start]
        suffix = text[end + len(END_MARKER) :]
        managed = text[start + len(START_MARKER) : end]
        return prefix, suffix, _parse_entries(managed)

    def _write(self, prefix: str, suffix: str, entries: list[MemoryEntry]) -> None:
        rendered = _render_memory(prefix, suffix, entries)
        if len(rendered.encode("utf-8")) > self.max_file_bytes:
            raise MemoryStoreError("MEMORY_LIMIT_EXCEEDED", "Memory file is too large.")
        _atomic_write(self.context.durable_file, rendered)

    def _validate_content(self, content: str) -> str:
        if not isinstance(content, str):
            raise MemoryStoreError("MEMORY_INVALID_OPERATION", "Memory content must be text.")
        normalized = " ".join(content.split()).strip()
        if not normalized:
            raise MemoryStoreError("MEMORY_INVALID_OPERATION", "Memory content is required.")
        if len(normalized) > self.max_entry_chars:
            raise MemoryStoreError("MEMORY_LIMIT_EXCEEDED", "Memory entry is too long.")
        return normalized

    @staticmethod
    def _validate_category(category: str, *, allow_empty: bool = False) -> None:
        if allow_empty and category == "":
            return
        if category not in MEMORY_CATEGORIES:
            raise MemoryStoreError("MEMORY_INVALID_CATEGORY", "Memory category is invalid.")

    def _lock(self) -> threading.RLock:
        return _LOCKS[str(self.context.durable_file.resolve())]

def _parse_entries(managed: str) -> list[MemoryEntry]:
    entries: list[MemoryEntry] = []
    current_category = ""
    seen_categories: list[str] = []
    lines = managed.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line.startswith("## "):
            category = line[3:].strip()
            if category not in MEMORY_CATEGORIES or category in seen_categories:
                raise MemoryStoreError("MEMORY_FORMAT_INVALID", "Memory categories are invalid.")
            seen_categories.append(category)
            current_category = category
            index += 1
            continue
        match = _ENTRY_RE.fullmatch(line)
        if match is None or not current_category:
            raise MemoryStoreError("MEMORY_FORMAT_INVALID", "Memory entry format is invalid.")
        content = match.group(1).strip()
        if not content:
            raise MemoryStoreError("MEMORY_FORMAT_INVALID", "Memory entry content is invalid.")
        entries.append(
            MemoryEntry(
                category=current_category,
                content=content,
            )
        )
        index += 1
    if tuple(seen_categories) != MEMORY_CATEGORIES:
        raise MemoryStoreError("MEMORY_FORMAT_INVALID", "Memory categories are incomplete.")
    return entries


def _render_memory(prefix: str, suffix: str, entries: list[MemoryEntry]) -> str:
    by_category: dict[str, list[MemoryEntry]] = {category: [] for category in MEMORY_CATEGORIES}
    for entry in entries:
        by_category[entry.category].append(entry)
    lines = [START_MARKER, ""]
    for category in MEMORY_CATEGORIES:
        lines.extend([f"## {category}", ""])
        for entry in by_category[category]:
            lines.append(f"- {entry.content}")
            lines.append("")
    lines.append(END_MARKER)
    managed = "\n".join(lines)
    return f"{prefix}{managed}{suffix}"


def _default_template() -> str:
    headings = "\n\n".join(f"## {category}" for category in MEMORY_CATEGORIES)
    return f"# ZhiCe-Agent Memory\n\n{START_MARKER}\n\n{headings}\n\n{END_MARKER}\n"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        _replace_with_retry(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _replace_with_retry(source: Path, target: Path) -> None:
    """Tolerate short Windows scanner/indexer locks around atomic replacement."""

    delays = (0.01, 0.03, 0.06)
    for delay in delays:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(delay)
    os.replace(source, target)


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _score(query: str, content: str) -> float:
    normalized = _normalize(content)
    if query == normalized:
        return 1000.0
    if query in normalized:
        return 500.0 + len(query) / max(1, len(normalized))
    query_terms = _terms(query)
    content_terms = _terms(normalized)
    if not query_terms or not content_terms:
        return 0.0
    overlap = len(query_terms & content_terms)
    return overlap / len(query_terms) if overlap else 0.0


def _terms(value: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9_]+", value))
    cjk_chunks = re.findall(r"[\u3400-\u9fff]+", value)
    for chunk in cjk_chunks:
        if len(chunk) == 1:
            words.add(chunk)
        else:
            words.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return words


__all__ = ["MarkdownMemoryStore", "MemoryStoreError"]
