import time

import pytest

from agent.context.config import RetrievalConfig
from agent.context.index import SQLiteTurnSearchIndex
from agent.context.retrieval import fuse_hits, rewrite_query_once
from agent.protocols.context import SearchHit, TurnDocument
from agent.protocols.llm import LLMResponse


def _document(session: str, index: int, text: str, *, entities=(), anchors=()):
    return TurnDocument(
        session_id=session,
        turn_id=f"turn-{index}",
        turn_index=index,
        user_text=text,
        assistant_text="answer",
        tool_text="",
        entities=tuple(entities),
        anchors=tuple(anchors),
        content_hash=f"hash-{index}-{text}",
    )


def test_sqlite_fts_and_exact_cosine_are_session_scoped(tmp_path):
    index = SQLiteTurnSearchIndex(tmp_path / "context_index.sqlite3")
    alpha = _document("same", 1, "Newton gravity", entities=("Newton",))
    beta = _document("same", 2, "Python dataclass")
    other = _document("other", 1, "Newton secret other user")
    index.upsert([alpha, beta, other])
    index.upsert_embeddings([alpha, beta], [[1.0, 0.0], [0.0, 1.0]], "fake:v1")

    hits = index.search(
        "same",
        "Newton",
        query_embedding=[1.0, 0.0],
        provider_identity="fake:v1",
    )

    assert {hit.document.session_id for hit in hits} == {"same"}
    alpha_hit = next(hit for hit in hits if hit.document.turn_id == "turn-1")
    assert alpha_hit.lexical_rank == 1
    assert alpha_hit.semantic_rank == 1
    assert alpha_hit.semantic_score == 1.0


def test_exact_substring_recalls_chinese_without_fts_token_assumptions(tmp_path):
    index = SQLiteTurnSearchIndex(tmp_path / "context_index.sqlite3")
    index.upsert([_document("session", 1, "请介绍一下牛顿")])

    hits = index.search("session", "牛顿")

    assert [hit.document.turn_id for hit in hits] == ["turn-1"]


def test_index_operations_release_database_file_handle(tmp_path):
    path = tmp_path / "context_index.sqlite3"
    index = SQLiteTurnSearchIndex(path)
    index.upsert([_document("session", 1, "release handle")])

    assert index.search("session", "release")

    path.unlink()
    assert not path.exists()


def test_rank_fusion_honors_exact_entity_and_anchor():
    document = _document(
        "session",
        7,
        "failure details",
        entities=("ContextBuilder",),
        anchors=("agent/core/context.py", "ValueError"),
    )
    ranked = fuse_hits(
        "ContextBuilder ValueError agent/core/context.py",
        [SearchHit(document=document, lexical_rank=3, semantic_rank=9, semantic_score=0.4)],
        RetrievalConfig(top_k=2),
        max_turn_index=10,
    )

    assert ranked[0].entity_match is True
    assert ranked[0].anchor_match is True
    assert "anchor_exact" in ranked[0].reason


def test_recency_bonus_cannot_displace_strong_old_semantic_match():
    old_match = _document("session", 1, "the remembered project passphrase")
    recent_noise = [
        _document("session", index, f"unrelated recent discussion {index}")
        for index in range(15, 21)
    ]
    hits = [SearchHit(document=old_match, semantic_rank=1, semantic_score=0.73)]
    hits.extend(
        SearchHit(document=document, semantic_rank=rank, semantic_score=0.46)
        for rank, document in enumerate(recent_noise, start=2)
    )

    ranked = fuse_hits(
        "what was the project passphrase",
        hits,
        RetrievalConfig(top_k=6),
        max_turn_index=20,
    )

    assert ranked[0].turn_id == old_match.turn_id
    assert old_match.turn_id in {item.turn_id for item in ranked}


def test_embedding_identity_switch_marks_vectors_missing(tmp_path):
    index = SQLiteTurnSearchIndex(tmp_path / "context_index.sqlite3")
    document = _document("session", 1, "semantic text")
    index.upsert([document])
    index.upsert_embeddings([document], [[0.1, 0.2]], "fake:v1")

    assert index.missing_embeddings("session", "fake:v1") == []
    assert [item.turn_id for item in index.missing_embeddings("session", "fake:v2")] == [
        "turn-1"
    ]


def test_delete_session_does_not_delete_other_session(tmp_path):
    index = SQLiteTurnSearchIndex(tmp_path / "context_index.sqlite3")
    index.upsert([_document("one", 1, "alpha"), _document("two", 1, "beta")])

    index.delete_session("one")

    assert index.search("one", "alpha") == []
    assert [hit.document.session_id for hit in index.search("two", "beta")] == ["two"]


def test_corrupt_sqlite_is_quarantined_and_rebuildable(tmp_path):
    path = tmp_path / "context_index.sqlite3"
    path.write_bytes(b"not-a-sqlite-database")

    index = SQLiteTurnSearchIndex(path)
    index.upsert([_document("session", 1, "rebuilt truth")])

    assert index.recovered is True
    assert index.search("session", "rebuilt truth")
    assert list(tmp_path.glob("context_index.sqlite3.corrupt-*"))


def test_same_session_id_in_two_user_indexes_is_physically_isolated(tmp_path):
    first = SQLiteTurnSearchIndex(tmp_path / "users" / "one" / "context" / "context_index.sqlite3")
    second = SQLiteTurnSearchIndex(tmp_path / "users" / "two" / "context" / "context_index.sqlite3")
    first.upsert([_document("same-session", 1, "alpha-exclusive")])
    second.upsert([_document("same-session", 1, "zeta-exclusive")])

    assert first.search("same-session", "alpha-exclusive")
    assert first.search("same-session", "zeta-exclusive") == []
    assert second.search("same-session", "zeta-exclusive")


@pytest.mark.parametrize("count", [100, 1000])
def test_exact_cosine_scan_is_bounded_for_small_and_medium_sessions(tmp_path, count):
    index = SQLiteTurnSearchIndex(tmp_path / "context_index.sqlite3")
    documents = [_document("session", number, f"turn text {number}") for number in range(1, count + 1)]
    vectors = [[float(number % 7), 1.0, 0.5, 0.25] for number in range(1, count + 1)]
    index.upsert(documents)
    index.upsert_embeddings(documents, vectors, "fake:v1")

    started = time.perf_counter()
    hits = index.search(
        "session",
        "unmatched query",
        query_embedding=[1.0, 1.0, 0.5, 0.25],
        provider_identity="fake:v1",
        top_k=20,
    )

    assert len(hits) == 20
    assert time.perf_counter() - started < 5.0


@pytest.mark.integration
def test_ten_thousand_turn_exact_cosine_p95(tmp_path):
    index = SQLiteTurnSearchIndex(tmp_path / "context_index.sqlite3")
    documents = [_document("session", number, f"history {number}") for number in range(1, 10001)]
    vectors = [
        [float((number + offset) % 11) / 10.0 for offset in range(32)]
        for number in range(1, 10001)
    ]
    index.upsert(documents)
    index.upsert_embeddings(documents, vectors, "fake:v1")
    timings = []
    for _ in range(7):
        started = time.perf_counter()
        hits = index.search(
            "session",
            "no lexical match",
            query_embedding=[0.5] * 32,
            provider_identity="fake:v1",
            top_k=20,
        )
        timings.append(time.perf_counter() - started)

    assert len(hits) == 20
    p95 = sorted(timings)[-1]
    assert p95 < 0.1, f"10k exact cosine p95 was {p95:.4f}s"


def test_elliptical_query_rewrite_is_bounded_and_does_not_answer():
    class RewriteLLM:
        def chat(self, messages, tools=None):
            return LLMResponse(content='{"query":"ContextBuilder 的预算逻辑"}')

    rewritten = rewrite_query_once(
        "继续说这个",
        ["上一轮讨论 ContextBuilder"],
        RewriteLLM(),
        "rewrite only",
    )

    assert rewritten == "ContextBuilder 的预算逻辑"
