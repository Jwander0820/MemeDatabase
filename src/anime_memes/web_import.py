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


def fetch_image(url):
    with urlopen(Request(url, headers={"User-Agent": "AnimeMemes/1.0"}), timeout=25) as response:
        data = response.read(32 * 1024 * 1024 + 1)
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
    return FileStorage(stream, filename=f"web-meme.{suffix}")


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
                if not url:
                    raise ValueError("缺少圖片直連")
                if url in seen:
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
                with closing(fetch_image(url)) as upload:
                    if args.apply:
                        entry = create_manual_entry(conn, form, upload, args.attachment_dir, args.thumbnail_dir,
                                                    web_image_url=url)
                        result.update(status="imported", key=entry["key"])
                    else:
                        result.update(status="ready")
                seen.add(url)
            except HTTPError as exc:
                result.update(status="error", error=f"HTTP {exc.code}")
                exc.close()
            except (OSError, HTTPException, ValueError, sqlite3.Error, Image.DecompressionBombError) as exc:
                result.update(status="error", error=str(exc))
    return results
