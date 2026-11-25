from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _opencc_converter():
    try:
        from opencc import OpenCC  # type: ignore
    except Exception:
        return None
    try:
        return OpenCC("s2twp")
    except Exception:
        try:
            return OpenCC("s2t")
        except Exception:
            return None


def dedupe(values):
    """去除重複並保留第一次出現的順序。"""
    return list(dict.fromkeys(values))


def to_traditional(value: str | None) -> str:
    text = value or ""
    converter = _opencc_converter()
    if converter is None:
        return text
    try:
        return str(converter.convert(text))
    except Exception:
        return text

