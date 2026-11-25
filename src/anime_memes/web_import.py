"""由本機 CLI 讀取人工核准的網路梗圖清單。"""
import json
import sqlite3
from contextlib import ExitStack, closing
from http.client import HTTPException
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image
from werkzeug.datastructures import FileStorage

from .db import connect, init_db
from .management import create_manual_entry, source_page_url


def _validate_image_bytes(data, filename_stem):
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("圖片超過 32 MiB")
    stream = BytesIO(data)
    with Image.open(stream) as img:
        suffix = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp"}.get(img.format)
        if not suffix:
            raise ValueError("不支援的圖片格式")
        img.verify()
    stream.seek(0)
    with Image.open(stream) as img:
        img.load()
    stream.seek(0)
    return FileStorage(stream, filename=f"{filename_stem}.{suffix}")


def fetch_image(url):
    with urlopen(Request(url, headers={"User-Agent": "MemeDatabase/1.0"}), timeout=25) as response:
        data = response.read(32 * 1024 * 1024 + 1)
    return _validate_image_bytes(data, "web-meme")


def read_local_image(local_root, rel_path):
    root = Path(local_root).resolve()
    target = (root / rel_path).resolve()
    if root not in target.parents:
        raise ValueError("本機路徑超出指定根目錄")
    if not target.is_file():
        raise ValueError(f"找不到本機圖片：{rel_path}")
    if target.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("圖片超過 32 MiB")
    return _validate_image_bytes(target.read_bytes(), "local-meme")


def import_approved(args):
    lines = args.approved_jsonl.read_text(encoding="utf-8-sig").splitlines()
    results = []
    seen = set()
    with ExitStack() as stack:
        conn = None
        if args.apply:
            conn = stack.enter_context(closing(connect(args.db)))
            init_db(conn)
        elif Path(args.db).is_file():
            uri = Path(args.db).resolve().as_uri() + "?mode=ro"
            conn = stack.enter_context(closing(sqlite3.connect(uri, uri=True)))
        if conn and any(r[1] == "web_image_url" for r in conn.execute("PRAGMA table_info(meme_entries)")):
            seen.update(r[0] for r in conn.execute("SELECT web_image_url FROM meme_entries WHERE web_image_url IS NOT NULL"))
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            result = {"line": number}
            results.append(result)
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("每行須為 JSON 物件")
                url = source_page_url(row.get("image_url"))
                local_rel = row.get("local_path")
                local_root = getattr(args, "local_root", None)
                if url:
                    dedup_key = url
                elif isinstance(local_rel, str) and local_rel.strip() and local_root:
                    dedup_key = f"local:{local_rel}"
                else:
                    raise ValueError("缺少圖片直連或本機路徑")
                if dedup_key in seen:
                    result.update(status="duplicate")
                    continue
                tags = row.get("tags")
                if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                    raise ValueError("tags 須為文字陣列")
                form = {"source_page": source_page_url(row.get("source_page")), "tags": "\n".join(tags)}
                for src, dest in [("subject", "anime_title"), ("caption", "quote_text"),
                                  ("display_title", "display_title"), ("speaker", "speaker")]:
                    value = row.get(src)
                    if value is not None and not isinstance(value, str):
                        raise ValueError(f"{src} 須為文字或 null")
                    form[dest] = value or ""
                loader = fetch_image(url) if url else read_local_image(local_root, local_rel)
                with closing(loader) as upload:
                    if args.apply:
                        entry = create_manual_entry(conn, form, upload, args.attachment_dir, args.thumbnail_dir,
                                                    web_image_url=url)
                        result.update(status="imported", key=entry["key"])
                    else:
                        result.update(status="ready")
                seen.add(dedup_key)
            except HTTPError as exc:
                result.update(status="error", error=f"HTTP {exc.code}")
                exc.close()
            except (OSError, HTTPException, ValueError, sqlite3.Error, Image.DecompressionBombError) as exc:
                result.update(status="error", error=str(exc))
    return results
