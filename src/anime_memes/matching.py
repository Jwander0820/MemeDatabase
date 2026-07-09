from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any


URL_RE = re.compile(r"https?://\S+")
EMOJI_ONLY_RE = re.compile(r"^(:[\w.-]+:|\s)+$")


@dataclass(frozen=True)
class ParsedText:
    anime_title: str | None
    speaker: str | None
    quote_text: str
    confidence_delta: float


@dataclass(frozen=True)
class Pairing:
    text_message: dict[str, Any] | None
    method: str
    confidence: float
    reason: str | None = None


def clean_content(content: str | None) -> str:
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = URL_RE.sub("", text).strip()
    return text


def is_noise(content: str | None) -> bool:
    text = clean_content(content)
    if not text:
        return True
    if EMOJI_ONLY_RE.match(text):
        return True
    return len(text) <= 2 and not any("\u4e00" <= char <= "\u9fff" for char in text)


def parse_meme_text(content: str | None) -> ParsedText:
    lines = [line.strip() for line in clean_content(content).split("\n") if line.strip()]
    if not lines:
        return ParsedText(None, None, "", -0.5)
    if len(lines) == 1:
        return ParsedText(None, None, lines[0], -0.2)
    if len(lines) == 2:
        return ParsedText(lines[0], None, lines[1], -0.1)
    return ParsedText(lines[0], lines[1], "\n".join(lines[2:]), 0.0)


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def pair_text_for_attachment(
    messages: list[dict[str, Any]],
    index: int,
    used_text_message_ids: set[str],
    window: int = 3,
) -> Pairing:
    message = messages[index]
    if not is_noise(message.get("content")):
        return Pairing(message, "same_message", 0.95, None)

    author_id = (message.get("author") or {}).get("id")
    base_time = parse_timestamp(message["timestamp"])
    candidates: list[tuple[float, int, dict[str, Any]]] = []
    for offset in range(1, window + 1):
        for candidate_index in (index - offset, index + offset):
            if candidate_index < 0 or candidate_index >= len(messages):
                continue
            candidate = messages[candidate_index]
            candidate_id = candidate.get("id")
            if candidate_id in used_text_message_ids:
                continue
            if (candidate.get("author") or {}).get("id") != author_id:
                continue
            if candidate.get("attachments") and candidate.get("content"):
                continue
            if is_noise(candidate.get("content")):
                continue
            delta = abs((parse_timestamp(candidate["timestamp"]) - base_time).total_seconds())
            direction_penalty = 0 if candidate_index < index else 5
            candidates.append((delta + direction_penalty, candidate_index, candidate))

    if not candidates:
        return Pairing(None, "unpaired", 0.2, "no nearby same-author text")

    _, candidate_index, candidate = sorted(candidates, key=lambda item: item[0])[0]
    direction = "previous" if candidate_index < index else "next"
    distance = abs(candidate_index - index)
    confidence = max(0.45, 0.86 - (distance - 1) * 0.08)
    return Pairing(candidate, f"nearby_{direction}", confidence, None)
