"""共用搜尋規則；即時計算避免匯入或 OCR 更新後出現過期投影。"""
from __future__ import annotations

import json
import unicodedata
from functools import lru_cache

from .text import to_traditional

NORMALIZATION_VERSION = 1


@lru_cache(maxsize=8192)
def normalize(value: str) -> str:
    value = to_traditional(unicodedata.normalize("NFKC", value).casefold())
    return "".join(c for c in value if not c.isspace() and
                   (c in "%_" or not unicodedata.category(c).startswith("P")))


def prepare_query(value: str) -> tuple[str, tuple[str, ...]]:
    if len(value) > 256:
        raise ValueError("搜尋文字不可超過 256 字")
    whole = normalize(value)
    if value and not whole:
        raise ValueError("請輸入有效搜尋文字")
    terms = tuple(dict.fromkeys(normalize(t) for t in value.split() if normalize(t)))
    if len(terms) > 16:
        raise ValueError("搜尋詞不可超過 16 個")
    return whole, terms


def validate_aliases(values: object) -> list[tuple[str, str]]:
    if not isinstance(values, list) or len(values) > 50:
        raise ValueError("search_aliases 必須是最多 50 筆的文字陣列")
    result = {}
    for value in values:
        if not isinstance(value, str) or len(value) > 200 or not normalize(value):
            raise ValueError("替代詞必須為有效文字且不可超過 200 字")
        result.setdefault(normalize(value), value.strip())
    return [(text, normalized) for normalized, text in result.items()]


def replace_aliases(conn, key: str, aliases: list[tuple[str, str]]) -> None:
    conn.execute("DELETE FROM entry_search_aliases WHERE entry_key=?", (key,))
    conn.executemany(
        "INSERT INTO entry_search_aliases(entry_key,alias_text,alias_normalized,normalization_version) VALUES(?,?,?,?)",
        [(key, text, normalized, NORMALIZATION_VERSION) for text, normalized in aliases],
    )


def scorer(query: str):
    whole, terms = prepare_query(query)

    def score(title, speaker, work, quote, ocr, tags, aliases):
        fields = [(normalize(title or ""), 500), (normalize(speaker or ""), 300),
                  (normalize(work or ""), 300), (normalize(quote or ""), 200),
                  (normalize(ocr or ""), 50)]
        fields += [(normalize(v), 400) for v in json.loads(tags or "[]") + json.loads(aliases or "[]")]

        def best(term):
            return max((weight if term == text else min(weight, 100)
                        for text, weight in fields if term and term in text), default=0)

        scores = [best(t) for t in terms]
        phrase = best(whole)
        if not phrase and not (scores and all(scores)):
            return 0
        # 每個詞僅取最佳欄位；完整片語優先，同分由 SQL 穩定排序。
        exact = max((weight for text, weight in fields if whole == text), default=0)
        return exact * 10000 + max(phrase, sum(scores))

    return score


ALIASES_SQL = """(SELECT json_group_array(alias_text) FROM
    (SELECT alias_text FROM entry_search_aliases WHERE entry_key=e.key ORDER BY alias_normalized))"""
TAGS_SQL = "(SELECT json_group_array(DISTINCT tag_name) FROM entry_tags WHERE entry_key=e.key)"
SCORE_SQL = f"meme_score(e.display_title,e.speaker,e.anime_title,e.quote_text,e.ocr_text,{TAGS_SQL},{ALIASES_SQL})"
