"""SQLite FTS5/BM25 metadata and exact local cosine Turn index."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import struct
import time
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from agent.protocols.context import SearchHit, TurnDocument


class SQLiteTurnSearchIndex:
    """A small user-isolated index whose complete source remains Session JSONL."""

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.recovered = False
        try:
            self._initialize()
        except sqlite3.DatabaseError:
            self._quarantine_corrupt_files()
            self._initialize()
            self.recovered = True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")
        except Exception:
            connection.close()
            raise
        return connection

    @contextmanager
    def _connection(self):
        """Commit or roll back one operation and always release Windows file handles."""

        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS turn_documents (
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    user_text TEXT NOT NULL,
                    assistant_text TEXT NOT NULL,
                    tool_text TEXT NOT NULL,
                    entities_json TEXT NOT NULL,
                    anchors_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    embedding BLOB,
                    embedding_dim INTEGER,
                    provider_identity TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (session_id, turn_id)
                );
                CREATE INDEX IF NOT EXISTS idx_turn_documents_session_index
                    ON turn_documents(session_id, turn_index);
                CREATE VIRTUAL TABLE IF NOT EXISTS turn_documents_fts USING fts5(
                    session_id UNINDEXED,
                    turn_id UNINDEXED,
                    searchable_text,
                    tokenize='unicode61'
                );
                """
            )

    def _quarantine_corrupt_files(self) -> None:
        """Move rebuildable corrupt SQLite artifacts aside without touching JSONL truth."""

        stamp = int(time.time() * 1000)
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(self.path) + suffix)
            if source.exists():
                target = source.with_name(f"{source.name}.corrupt-{stamp}")
                os.replace(source, target)

    def upsert(self, documents: Sequence[TurnDocument]) -> None:
        if not documents:
            return
        with self._connection() as db:
            for document in documents:
                existing = db.execute(
                    "SELECT content_hash FROM turn_documents WHERE session_id=? AND turn_id=?",
                    (document.session_id, document.turn_id),
                ).fetchone()
                changed = existing is None or str(existing["content_hash"]) != document.content_hash
                db.execute(
                    """
                    INSERT INTO turn_documents(
                        session_id, turn_id, turn_index, user_text, assistant_text, tool_text,
                        entities_json, anchors_json, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, turn_id) DO UPDATE SET
                        turn_index=excluded.turn_index,
                        user_text=excluded.user_text,
                        assistant_text=excluded.assistant_text,
                        tool_text=excluded.tool_text,
                        entities_json=excluded.entities_json,
                        anchors_json=excluded.anchors_json,
                        content_hash=excluded.content_hash,
                        embedding=CASE WHEN turn_documents.content_hash=excluded.content_hash
                            THEN turn_documents.embedding ELSE NULL END,
                        embedding_dim=CASE WHEN turn_documents.content_hash=excluded.content_hash
                            THEN turn_documents.embedding_dim ELSE NULL END,
                        provider_identity=CASE WHEN turn_documents.content_hash=excluded.content_hash
                            THEN turn_documents.provider_identity ELSE '' END
                    """,
                    (
                        document.session_id,
                        document.turn_id,
                        document.turn_index,
                        document.user_text,
                        document.assistant_text,
                        document.tool_text,
                        json.dumps(document.entities, ensure_ascii=False),
                        json.dumps(document.anchors, ensure_ascii=False),
                        document.content_hash,
                    ),
                )
                if changed:
                    db.execute(
                        "DELETE FROM turn_documents_fts WHERE session_id=? AND turn_id=?",
                        (document.session_id, document.turn_id),
                    )
                    db.execute(
                        "INSERT INTO turn_documents_fts(session_id, turn_id, searchable_text) VALUES (?, ?, ?)",
                        (document.session_id, document.turn_id, document.searchable_text),
                    )

    def search(
        self,
        session_id: str,
        query: str,
        *,
        query_embedding: Sequence[float] | None = None,
        provider_identity: str = "",
        top_k: int = 20,
    ) -> list[SearchHit]:
        lexical: dict[str, int] = {}
        semantic: dict[str, tuple[int, float]] = {}
        documents: dict[str, TurnDocument] = {}
        with self._connection() as db:
            expression = _fts_expression(query)
            if expression:
                rows = db.execute(
                    """
                    SELECT d.*, bm25(turn_documents_fts) AS score
                    FROM turn_documents_fts
                    JOIN turn_documents d USING(session_id, turn_id)
                    WHERE turn_documents_fts MATCH ? AND d.session_id=?
                    ORDER BY score ASC LIMIT ?
                    """,
                    (expression, session_id, max(1, top_k)),
                ).fetchall()
                for rank, row in enumerate(rows, start=1):
                    lexical[str(row["turn_id"])] = rank
                    documents[str(row["turn_id"])] = _document(row)
            query_folded = query.casefold().strip()
            query_terms = {
                term.casefold()
                for term in query.replace("/", " ").replace("\\", " ").split()
                if len(term) >= 2
            }
            exact_clauses = [
                "lower(user_text || char(10) || assistant_text || char(10) || tool_text) LIKE ?"
            ]
            exact_parameters: list[object] = [f"%{query_folded}%"]
            for term in sorted(query_terms):
                exact_clauses.append(
                    "(lower(entities_json) LIKE ? OR lower(anchors_json) LIKE ?)"
                )
                exact_parameters.extend((f'%"{term}"%', f'%"{term}"%'))
            if query_folded:
                exact_rows = db.execute(
                    f"""SELECT * FROM turn_documents WHERE session_id=? AND
                    ({' OR '.join(exact_clauses)}) ORDER BY turn_index DESC LIMIT ?""",
                    (session_id, *exact_parameters, max(20, top_k * 4)),
                ).fetchall()
                for row in exact_rows:
                    document = _document(row)
                    documents[document.turn_id] = document
            if query_embedding is not None and provider_identity:
                rows = db.execute(
                    """SELECT turn_id, embedding, embedding_dim FROM turn_documents WHERE session_id=?
                    AND provider_identity=? AND embedding IS NOT NULL""",
                    (session_id, provider_identity),
                ).fetchall()
                scored = _vectorized_cosine_rows(rows, query_embedding)
                for rank, (score, row) in enumerate(
                    sorted(scored, key=lambda item: item[0], reverse=True)[: max(1, top_k)],
                    start=1,
                ):
                    turn_id = str(row["turn_id"])
                    semantic[turn_id] = (rank, score)
                    if turn_id not in documents:
                        document_row = db.execute(
                            "SELECT * FROM turn_documents WHERE session_id=? AND turn_id=?",
                            (session_id, turn_id),
                        ).fetchone()
                        if document_row is not None:
                            documents[turn_id] = _document(document_row)
        return [
            SearchHit(
                document=document,
                lexical_rank=lexical.get(turn_id),
                semantic_rank=semantic.get(turn_id, (None, 0.0))[0],
                semantic_score=(semantic[turn_id][1] if turn_id in semantic else None),
            )
            for turn_id, document in documents.items()
        ]

    def missing_embeddings(self, session_id: str, provider_identity: str) -> list[TurnDocument]:
        with self._connection() as db:
            rows = db.execute(
                """SELECT * FROM turn_documents WHERE session_id=? AND
                (embedding IS NULL OR provider_identity<>?) ORDER BY turn_index""",
                (session_id, provider_identity),
            ).fetchall()
        return [_document(row) for row in rows]

    def upsert_embeddings(
        self,
        documents: Sequence[TurnDocument],
        vectors: Sequence[Sequence[float]],
        provider_identity: str,
    ) -> None:
        if len(documents) != len(vectors):
            raise ValueError("embedding document/vector count mismatch")
        with self._connection() as db:
            for document, vector in zip(documents, vectors, strict=True):
                db.execute(
                    """UPDATE turn_documents SET embedding=?, embedding_dim=?, provider_identity=?
                    WHERE session_id=? AND turn_id=? AND content_hash=?""",
                    (
                        _pack_vector(vector),
                        len(vector),
                        provider_identity,
                        document.session_id,
                        document.turn_id,
                        document.content_hash,
                    ),
                )

    def delete_session(self, session_id: str) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM turn_documents_fts WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM turn_documents WHERE session_id=?", (session_id,))

    def rebuild_session(self, session_id: str, documents: Sequence[TurnDocument]) -> None:
        self.delete_session(session_id)
        self.upsert(documents)


def _document(row: sqlite3.Row) -> TurnDocument:
    return TurnDocument(
        session_id=str(row["session_id"]),
        turn_id=str(row["turn_id"]),
        turn_index=int(row["turn_index"]),
        user_text=str(row["user_text"]),
        assistant_text=str(row["assistant_text"]),
        tool_text=str(row["tool_text"]),
        entities=tuple(json.loads(row["entities_json"] or "[]")),
        anchors=tuple(json.loads(row["anchors_json"] or "[]")),
        content_hash=str(row["content_hash"]),
    )


def _fts_expression(query: str) -> str:
    terms = []
    for raw in query.replace('"', " ").split():
        term = "".join(char for char in raw if char.isalnum() or char in "_-.\\/")
        if len(term) >= 2:
            terms.append(f'"{term}"')
    if not terms and len(query.strip()) >= 2:
        terms = [f'"{query.strip()}"']
    return " OR ".join(terms[:20])


def _pack_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *(float(value) for value in vector))


def _unpack_vector(blob: bytes, dimensions: int) -> tuple[float, ...]:
    if not blob or dimensions <= 0 or len(blob) != dimensions * 4:
        return ()
    return struct.unpack(f"<{dimensions}f", blob)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _vectorized_cosine_rows(
    rows: Sequence[sqlite3.Row],
    query: Sequence[float],
) -> list[tuple[float, sqlite3.Row]]:
    """Compute exact float32 cosine scores in one bounded NumPy matrix."""

    dimensions = len(query)
    valid_rows = [
        row
        for row in rows
        if int(row["embedding_dim"] or 0) == dimensions
        and len(row["embedding"] or b"") == dimensions * 4
    ]
    if not valid_rows or dimensions == 0:
        return []
    matrix = np.frombuffer(
        b"".join(bytes(row["embedding"]) for row in valid_rows),
        dtype="<f4",
    ).reshape(len(valid_rows), dimensions)
    query_vector = np.asarray(query, dtype=np.float32)
    query_norm = float(np.linalg.norm(query_vector))
    if query_norm == 0:
        return [(0.0, row) for row in valid_rows]
    row_norms = np.linalg.norm(matrix, axis=1)
    denominators = row_norms * query_norm
    scores = np.divide(
        matrix @ query_vector,
        denominators,
        out=np.zeros(len(valid_rows), dtype=np.float32),
        where=denominators != 0,
    )
    return [(float(score), row) for score, row in zip(scores, valid_rows, strict=True)]
