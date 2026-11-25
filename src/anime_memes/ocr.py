from __future__ import annotations

import os
from functools import lru_cache
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .text import dedupe, to_traditional


def normalize_text(value: str | None) -> str:
    return "".join(ch.lower() for ch in to_traditional(value) if not ch.isspace())


def similarity(left: str | None, right: str | None) -> float:
    a = normalize_text(left)
    b = normalize_text(right)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def run_ocr(
    image_path: str | Path,
    engine: str = "paddle",
    languages: list[str] | None = None,
    device: str = "auto",
) -> tuple[str, str]:
    if engine == "none":
        return "", "skipped: ocr disabled"
    if engine == "easyocr":
        return run_easyocr(image_path, languages)
    if engine == "paddle":
        return run_paddleocr(image_path, device=device)
    return "", f"skipped: unknown ocr engine ({engine})"


def run_paddleocr(image_path: str | Path, device: str = "auto") -> tuple[str, str]:
    path = Path(image_path)
    if not path.exists():
        return "", "skipped: missing local image"
    try:
        ensure_ocr_cache_env()
        device_status = configure_paddle_device(device)
        if device_status.startswith("error:"):
            return "", f"skipped: {device_status}"
        ocr = _paddle_reader(device_status)
        try:
            result = ocr.ocr(str(path), cls=True)
        except TypeError:
            result = ocr.ocr(str(path))
        text = to_traditional("\n".join(extract_paddle_text(result)))
        return text, f"ok:paddleocr:{device_status}"
    except Exception as exc:
        return "", f"skipped: paddleocr unavailable ({exc})"


@lru_cache(maxsize=4)
def _paddle_reader(device_status: str = "cpu") -> Any:
    from paddleocr import PaddleOCR  # type: ignore

    for kwargs in (
        {
            "lang": "chinese_cht",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
        },
        {
            "use_angle_cls": True,
            "lang": "chinese_cht",
            "use_gpu": device_status == "gpu",
            "show_log": False,
        },
        {
            "use_angle_cls": True,
            "lang": "chinese_cht",
            "use_gpu": device_status == "gpu",
        },
        {"lang": "chinese_cht"},
    ):
        try:
            return PaddleOCR(**kwargs)
        except Exception:
            continue
    return PaddleOCR()


def configure_paddle_device(device: str = "auto") -> str:
    import paddle  # type: ignore

    requested = (device or "auto").lower()
    cuda_available = bool(paddle.device.is_compiled_with_cuda())
    if requested == "gpu":
        if not cuda_available:
            return "error: gpu requested but installed paddlepaddle is not compiled with CUDA"
        paddle.set_device("gpu")
        return "gpu"
    if requested == "cpu":
        paddle.set_device("cpu")
        return "cpu"
    if requested == "auto" and cuda_available:
        paddle.set_device("gpu")
        return "gpu"
    paddle.set_device("cpu")
    return "cpu"


def ensure_ocr_cache_env(base_dir: str | Path = "models") -> None:
    base = Path(base_dir).resolve()
    defaults = {
        "HOME": base / "home",
        "USERPROFILE": base / "home",
        "PADDLE_PDX_CACHE_HOME": base / "paddlex",
        "HF_HOME": base / "huggingface",
        "MODELSCOPE_CACHE": base / "modelscope",
        "PADDLE_HOME": base / "paddle",
        "XDG_CACHE_HOME": base / "xdg",
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
        "PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT": "False",
        "FLAGS_use_mkldnn": "False",
    }
    for key, value in defaults.items():
        os.environ[key] = str(value)
    for key, value in defaults.items():
        if str(value) in {"True", "False"}:
            continue
        Path(value).mkdir(parents=True, exist_ok=True)


def extract_paddle_text(result: Any) -> list[str]:
    texts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            return
        if isinstance(value, dict):
            rec_texts = value.get("rec_texts")
            if isinstance(rec_texts, list):
                for item in rec_texts:
                    if isinstance(item, str) and item.strip():
                        texts.append(item.strip())
            for key in ("text", "rec_text", "transcription"):
                item = value.get(key)
                if isinstance(item, str) and item.strip():
                    texts.append(item.strip())
            for item in value.values():
                walk(item)
            return
        if isinstance(value, (list, tuple)):
            if len(value) >= 2 and isinstance(value[1], (list, tuple)):
                candidate = value[1][0] if value[1] else None
                if isinstance(candidate, str) and candidate.strip():
                    texts.append(candidate.strip())
            for item in value:
                walk(item)

    walk(result)
    return dedupe(texts)


def run_easyocr(image_path: str | Path, languages: list[str] | None = None) -> tuple[str, str]:
    path = Path(image_path)
    if not path.exists():
        return "", "skipped: missing local image"
    try:
        import easyocr  # type: ignore
    except Exception as exc:
        return "", f"skipped: easyocr unavailable ({exc})"

    langs = languages or ["ch_tra", "ja", "en"]
    reader = easyocr.Reader(langs, gpu=False)
    results = reader.readtext(str(path), detail=0, paragraph=True)
    text = "\n".join(str(item) for item in results if str(item).strip())
    return to_traditional(text), "ok:easyocr"
