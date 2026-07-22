"""Local relevance selection for session turn context."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from agent.protocols.session import TurnGroup

_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:\\-]+")
_CJK_CHUNK_RE = re.compile(r"[\u4e00-\u9fff]+")
_SPLIT_ANCHOR_RE = re.compile(r"[./:\\-]+")

_DEFAULT_MIN_SCORE = 0.24
_RECENCY_BONUS = 0.04
_CONFIRMATION_BONUS = 0.55
_FOLLOWUP_BONUS = 0.55
_MAX_TURN_TEXT_CHARS = 20000

_SHORT_CONFIRMATIONS = {
    "ok",
    "okay",
    "yes",
    "y",
    "sure",
    "好",
    "好的",
    "嗯",
    "嗯嗯",
    "行",
    "可以",
    "对",
    "是的",
    "继续",
    "生成吧",
    "就这样",
}

_CONFIRMATION_PROMPT_MARKERS = (
    "需要我",
    "要不要",
    "是否",
    "确认",
    "我可以",
    "你希望",
    "要我",
    "继续",
)

_CONTEXTUAL_FOLLOWUP_MARKERS = (
    "为什么没",
    "为什么没有",
    "为什么不",
    "什么原因",
    "怎么回事",
    "刚才",
    "刚刚",
    "我刚才问",
    "我刚刚问",
    "你刚刚说",
    "刚刚问",
    "刚刚说",
    "上一条",
    "上一轮",
    "上轮",
    "前一条",
    "前一轮",
    "上一个",
    "没调用",
    "没有调用",
    "这个呢",
    "那个呢",
    "whatdidijustask",
    "whatwasmylastquestion",
    "whatdidyoujustsay",
    "previousmessage",
    "lastquestion",
)


@dataclass(frozen=True)
class _ScoredTurn:
    index: int
    score: float
    turn: TurnGroup


def select_relevant_turns(
    query: str,
    candidate_turns: Iterable[TurnGroup],
    *,
    max_selected_turns: int = 3,
    min_score: float = _DEFAULT_MIN_SCORE,
) -> list[TurnGroup]:
    """Return candidate turns that are locally relevant to the current query."""

    if max_selected_turns <= 0:
        return []

    turns = list(candidate_turns)
    query = query.strip()
    if not query or not turns:
        return []

    query_features = _feature_weights(query)
    query_weight = sum(query_features.values())
    latest_index = len(turns) - 1
    scored: list[_ScoredTurn] = []

    for index, turn in enumerate(turns):
        turn_text = _turn_text(turn)
        score = _overlap_score(query_features, query_weight, turn_text)
        if score > 0:
            score += _RECENCY_BONUS * ((index + 1) / len(turns))
        if index == latest_index and _is_short_confirmation(query) and _latest_assistant_invites_reply(turn):
            score += _CONFIRMATION_BONUS
        if index == latest_index and _is_contextual_followup(query):
            score += _FOLLOWUP_BONUS
        if score >= min_score:
            scored.append(_ScoredTurn(index=index, score=score, turn=turn))

    strongest = sorted(scored, key=lambda item: (item.score, item.index), reverse=True)[
        :max_selected_turns
    ]
    return [item.turn for item in sorted(strongest, key=lambda item: item.index)]


def _overlap_score(
    query_features: Counter[str],
    query_weight: float,
    turn_text: str,
) -> float:
    if not query_features or query_weight <= 0:
        return 0.0

    turn_features = set(_feature_weights(turn_text).keys())
    overlap_weight = sum(weight for feature, weight in query_features.items() if feature in turn_features)
    if overlap_weight == 0:
        return 0.0
    return overlap_weight / query_weight


def _feature_weights(text: str) -> Counter[str]:
    """Extract weighted local lexical features from natural-language and code text."""

    features: Counter[str] = Counter()
    for token in _ascii_tokens(text):
        features[token] += _ascii_weight(token)
        for part in _split_anchor_parts(token):
            if part != token:
                features[part] += min(_ascii_weight(part), 1.0)

    for feature in _cjk_features(text):
        features[feature] += 1.0 if len(feature) > 1 else 0.5

    return features


def _ascii_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _ASCII_TOKEN_RE.finditer(text):
        token = match.group(0).casefold().strip("._-:/\\")
        if token:
            tokens.append(token)
    return tokens


def _split_anchor_parts(token: str) -> list[str]:
    if not any(separator in token for separator in (".", "/", "\\", "-", ":")):
        return []
    return [part for part in _SPLIT_ANCHOR_RE.split(token) if part]


def _ascii_weight(token: str) -> float:
    if not token:
        return 0.0
    if len(token) == 1:
        return 0.8
    if _is_anchor_token(token):
        return 2.0
    return 1.0


def _is_anchor_token(token: str) -> bool:
    return (
        any(char.isdigit() for char in token)
        or "_" in token
        or "." in token
        or "/" in token
        or "\\" in token
        or ":" in token
        or token.endswith(("error", "exception", "jsonl", "pytest"))
    )


def _cjk_features(text: str) -> list[str]:
    features: list[str] = []
    for match in _CJK_CHUNK_RE.finditer(text):
        chunk = match.group(0)
        if len(chunk) == 1:
            features.append(chunk)
            continue
        features.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return features


def _turn_text(turn: TurnGroup) -> str:
    parts: list[str] = []
    char_count = 0
    for message in turn.messages:
        content = message.content
        if not content:
            continue
        remaining = _MAX_TURN_TEXT_CHARS - char_count
        if remaining <= 0:
            break
        parts.append(content[:remaining])
        char_count += len(parts[-1])
    return "\n".join(parts)


def _is_short_confirmation(query: str) -> bool:
    compact = re.sub(r"[\s,，。.!！?？~～]+", "", query.casefold())
    return compact in _SHORT_CONFIRMATIONS


def _latest_assistant_invites_reply(turn: TurnGroup) -> bool:
    for message in reversed(turn.messages):
        if message.role != "assistant" or not message.content.strip():
            continue
        content = message.content.strip()
        return content.endswith(("?", "？")) or any(
            marker in content for marker in _CONFIRMATION_PROMPT_MARKERS
        )
    return False


def _is_contextual_followup(query: str) -> bool:
    """Identify short Chinese follow-ups whose referent lives in the latest Turn."""

    compact = re.sub(r"[\s,，。.!！?？~～]+", "", query.casefold())
    return len(compact) <= 32 and any(marker in compact for marker in _CONTEXTUAL_FOLLOWUP_MARKERS)
