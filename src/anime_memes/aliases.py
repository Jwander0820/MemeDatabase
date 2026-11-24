"""僅供管理端的替代詞驗證與寫入。"""
from .search import normalize,NORMALIZATION_VERSION

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
