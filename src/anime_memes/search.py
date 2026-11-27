"""共用搜尋規則。管理端即時正規化，避免編輯後出現過期投影；公開站快照不可變，每版預先正規化一次。

查詢語法（以空白分隔，可混用）：
    詞            任一欄位包含即可；多詞為 AND
    "片語"        視為單一詞，必須連續出現（也接受「」『』“”）
    -詞           排除任一欄位包含此詞的梗圖
    作品:詞       只比對指定欄位；可與 - 及引號併用，如 -作品:"某作品"
未知的欄位前綴（如 Re:ゼロ、12:30）照一般文字處理；要搜尋以 - 開頭的字面文字請加引號。
另可傳入搜尋範圍（API 的 field 參數），讓沒寫前綴的詞只比對該欄位。OCR 不參與搜尋。
"""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from .text import to_traditional

NORMALIZATION_VERSION = 1
MAX_QUERY_LENGTH = 256
MAX_TERMS = 16


@lru_cache(maxsize=32768)
def normalize(value: str) -> str:
    value = to_traditional(unicodedata.normalize("NFKC", value).casefold())
    return "".join(c for c in value if not c.isspace() and
                   (c in "%_" or not unicodedata.category(c).startswith("P")))


# 欄位權重即「完全相等」時的分數，也決定部分命中時的欄位加成；OCR 雜訊多，不參與搜尋。
WEIGHTS = {"title": 500, "tag": 400, "alias": 400, "speaker": 300, "work": 300, "quote": 200}
SEARCH_FIELDS = tuple(WEIGHTS)
EXACT_MIN = min(WEIGHTS.values())  # 完全相等的最低分（台詞），部分命中永遠低於此值
_FIELD_NAMES = {
    "title": ["名稱", "名称", "標題", "标题", "title", "name"],
    "speaker": ["角色", "人物", "speaker", "character", "char"],
    "work": ["作品", "番名", "動畫", "动画", "work", "anime"],
    "quote": ["台詞", "台词", "quote", "line"],
    "tag": ["標籤", "标签", "tag"],
    "alias": ["替代詞", "替代词", "別名", "别名", "alias"],
}
FIELD_ALIASES = {normalize(name): field for field, names in _FIELD_NAMES.items() for name in names}
QUOTE_PAIRS = {'"': '"', "「": "」", "『": "』", "“": "”"}


@dataclass(frozen=True)
class Term:
    text: str                      # 已正規化
    field: str | None = None       # None 表示所有欄位
    negative: bool = False
    phrase: bool = False


@dataclass(frozen=True)
class ParsedQuery:
    terms: tuple[Term, ...]
    whole: str                     # 沒寫前綴的正向詞依序緊縮，用於片語相鄰與整句完全相等
    free: tuple[Term, ...] = ()    # 沒寫前綴的正向詞（已套用搜尋範圍）
    scope: str | None = None       # 搜尋範圍；None 表示所有欄位

    @property
    def positives(self):
        return tuple(t for t in self.terms if not t.negative)

    @property
    def negatives(self):
        return tuple(t for t in self.terms if t.negative)


def search_field(value) -> str | None:
    """驗證 API 的搜尋範圍參數；空值表示所有欄位。"""
    if value in (None, ""):
        return None
    if value not in SEARCH_FIELDS:
        raise ValueError("搜尋範圍必須是 " + "、".join(SEARCH_FIELDS) + " 之一")
    return value


def _tokens(value: str):
    """逐字切出 (negative, field, raw_text, phrase)；不正規化內容。"""
    i, n = 0, len(value)
    while i < n:
        if value[i].isspace():
            i += 1
            continue
        negative = value[i] == "-" and i + 1 < n and not value[i + 1].isspace()
        if negative:
            i += 1
        field = None
        j = i
        while j < n and not value[j].isspace() and value[j] != ":" and value[j] not in QUOTE_PAIRS:
            j += 1
        if j < n and value[j] == ":" and j > i and normalize(value[i:j]) in FIELD_ALIASES:
            field, i = FIELD_ALIASES[normalize(value[i:j])], j + 1
        if i < n and value[i] in QUOTE_PAIRS:
            close = value.find(QUOTE_PAIRS[value[i]], i + 1)
            end = n if close < 0 else close
            yield negative, field, value[i + 1:end], True
            i = end + 1
        else:
            j = i
            while j < n and not value[j].isspace():
                j += 1
            yield negative, field, value[i:j], False
            i = j


@lru_cache(maxsize=256)
def parse_query(value: str, scope: str | None = None) -> ParsedQuery:
    scope = search_field(scope)
    if len(value) > MAX_QUERY_LENGTH:
        raise ValueError(f"搜尋文字不可超過 {MAX_QUERY_LENGTH} 字")
    value = unicodedata.normalize("NFKC", value)  # 全形 －：＂ 轉為半形語法符號
    terms, free, free_raw = {}, {}, []
    for negative, field, raw, phrase in _tokens(value):
        text = normalize(raw)
        if not text:
            continue  # 單獨的 -、空的「作品:」或純標點：輸入途中常見，直接略過
        term = Term(text, field or scope, negative, phrase)  # 明寫的前綴優先於搜尋範圍
        terms.setdefault(term, None)
        if not negative and field is None:
            free.setdefault(term, None)
            free_raw.append(raw)
    if value and not terms:
        raise ValueError("請輸入有效搜尋文字")
    if len(terms) > MAX_TERMS:
        raise ValueError(f"搜尋詞不可超過 {MAX_TERMS} 個")
    return ParsedQuery(tuple(terms), normalize(" ".join(free_raw)), tuple(free), scope)


def match_quality(term: str, text: str, weight: int) -> float:
    """單一詞對單一欄位值的命中品質；完全相等（200–500）永遠高於部分命中（60–100）。"""
    if not term or term not in text:
        return 0.0
    if term == text:
        return float(weight)
    coverage = len(term) / len(text)          # 詞佔欄位越多越像在描述這張圖
    prefix = 1.0 if text.startswith(term) else 0.0
    importance = (weight - EXACT_MIN) / (max(WEIGHTS.values()) - EXACT_MIN)
    return 60 + 40 * (0.4 * importance + 0.3 * prefix + 0.3 * coverage)


def normalized_fields(title, speaker, work, quote, tags, aliases, norm=normalize):
    """把一列的原始欄位轉成 ((欄位, 正規化文字), ...)；公開站每個發布版本只算一次（見 public_data）。"""
    fields = [("title", norm(title or "")), ("speaker", norm(speaker or "")),
              ("work", norm(work or "")), ("quote", norm(quote or ""))]
    fields += [("tag", norm(v)) for v in json.loads(tags or "[]")]
    fields += [("alias", norm(v)) for v in json.loads(aliases or "[]")]
    return tuple(fields)


def search_blob(fields) -> str:
    """整列正規化文字以換行串接；normalize 會移除空白，所以詞不會跨欄位誤中。"""
    return "\n".join(text for _, text in fields)


def field_scorer(query: str, field: str | None = None):
    """回傳 score(fields, blob=None)：對 normalized_fields 的結果計分，不再重複正規化。

    blob 為 search_blob(fields)；有給時先用整列字串快速排除不可能命中的列，結果不變。
    """
    parsed = parse_query(query, field)
    positives, negatives, free, scope = parsed.positives, parsed.negatives, parsed.free, parsed.scope
    required = tuple(t.text for t in positives if t.field)
    free_texts = tuple(t.text for t in free)

    def score(fields, blob=None):
        # 必要條件：有前綴的詞都要出現在某處；一般詞要全部出現，或整句（片語）出現。
        if blob is not None and positives and not (
                all(t in blob for t in required)
                and (all(t in blob for t in free_texts) or (len(free) > 1 and parsed.whole in blob))):
            return 0
        def scoped(term):
            return [text for name, text in fields if text and term.field in (None, name)]

        if any(term.text in text for term in negatives for text in scoped(term)):
            return 0
        if not positives:
            return 1  # 只有排除條件：其餘全部命中，同分交給 SQL 次排序

        def best(term_text, field=None):
            return max((match_quality(term_text, text, WEIGHTS[name])
                        for name, text in fields if field in (None, name)), default=0.0)

        scores = {term: best(term.text, term.field) for term in positives}
        # 兩個以上一般詞在同一欄位連續出現時加分，讓「相鄰」勝過「分散」。
        phrase = best(parsed.whole, scope) if len(free) > 1 else 0.0
        free_ok = all(scores[t] for t in free) or phrase > 0
        if not free_ok or not all(scores[t] for t in positives if t.field):
            return 0
        exact = max((WEIGHTS[name] for name, text in fields
                     if parsed.whole and text == parsed.whole and scope in (None, name)), default=0)
        # 整句與某欄位完全相等時置頂；其餘為各詞最佳欄位加總（每詞只算一次）＋片語加成。
        return exact * 10000 + sum(scores.values()) + phrase

    return score


def scorer(query: str, field: str | None = None):
    """管理端用：直接吃原始欄位（資料可能剛被編輯，不能預先正規化）。"""
    score_fields = field_scorer(query, field)

    @lru_cache(maxsize=4096)  # 同列在 WHERE 與 ORDER BY 各算一次
    def score(title, speaker, work, quote, tags, aliases):
        return score_fields(normalized_fields(title, speaker, work, quote, tags, aliases))

    return score


ALIASES_SQL = """(SELECT json_group_array(alias_text) FROM
    (SELECT alias_text FROM entry_search_aliases WHERE entry_key=e.key ORDER BY alias_normalized))"""
TAGS_SQL = "(SELECT json_group_array(DISTINCT tag_name) FROM entry_tags WHERE entry_key=e.key)"
SCORE_SQL = f"meme_score(e.display_title,e.speaker,e.anime_title,e.quote_text,{TAGS_SQL},{ALIASES_SQL})"
SCORE_ARGS = 6
