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


def to_traditional(value: str | None) -> str:
    text = value or ""
    converter = _opencc_converter()
    if converter is None:
        return text
    try:
        return str(converter.convert(text))
    except Exception:
        return text

