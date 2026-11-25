"""私人選用唯讀 MCP，獨立程序；不得加入公開映像。"""
from __future__ import annotations

from contextlib import closing
import hmac
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import settings
from .public_data import open_readonly
from .queries import query_entries, fetch_entry


def private_item(row, media_base):
    def text(value):
        return value[:4096] if isinstance(value, str) else value
    return {"id": row["key"], "display_title": text(row["display_title"]), "speaker": text(row["speaker"]),
            "anime_title": text(row["anime_title"]), "quote_text": text(row["quote_text"]),
            "tags": [t[:200] for t in sorted(set((row["tags"] or "").split(",")) - {""})[:30]],
            "search_aliases": [a[:200] for a in json.loads(row["search_aliases_json"])[:50]],
            "thumbnail_url": media_base + "/media/thumbnails/" + quote(row["attachment_id"], safe=""),
            "original_url": media_base + "/media/attachments/" + quote(row["attachment_id"], safe="")}


class PrivateSearch:
    def __init__(self, db, media_base):
        self.db, self.media_base = Path(db), media_base.rstrip("/")

    def search(self, query: str, limit: int = 5, field: str | None = None):
        if not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit 必須介於 1–20")
        with closing(open_readonly(self.db)) as conn:
            conn.execute("BEGIN")
            rows, total = query_entries(conn, {"q": query, "field": field, "status": "ready"}, 1, limit)
            return {"items": [private_item(r, self.media_base) for r in rows], "total": total}

    def get(self, entry_id: str):
        with closing(open_readonly(self.db)) as conn:
            row = fetch_entry(conn, entry_id)
            if row is None or row["status"] != "ready":
                raise ValueError("找不到項目")
            return private_item(row, self.media_base)


def create_mcp_app(db=None, token=None, media_base=None, allowed_hosts=None, allowed_origins=None):
    from mcp.server import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.responses import JSONResponse
    from starlette.middleware.base import BaseHTTPMiddleware

    token = token or os.environ.get("MEMES_MCP_TOKEN", "")
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError("啟用 MCP 必須設定至少 32 字元且無空白的 ASCII token")
    media_base = media_base or os.environ.get("MEMES_PRIVATE_URL", "http://127.0.0.1:3636")
    parsed = urlsplit(media_base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("私人媒體網址格式錯誤")
    service = PrivateSearch(db or settings.db_path(), media_base)
    server = MCPServer("AnimeMemes Private Search")

    @server.tool()
    def search_memes(query: str, limit: int = 5, field: str | None = None) -> dict:
        """依台詞、角色、作品或別名搜尋私人 ready 梗圖，回傳少量資料與圖片網址。

        query 以空白分隔多詞（AND）；支援 "片語"、-排除詞，以及欄位限定
        名稱:／角色:／作品:／台詞:／標籤:／別名:，例如 朋友 -作品:"某作品"。
        field 可選 work／speaker／quote／title／tag／alias，讓沒寫前綴的詞只比對該欄位。
        """
        return service.search(query, limit, field)

    @server.tool()
    def get_meme(id: str) -> dict:
        """依搜尋結果 ID 取得一筆私人 ready 梗圖。"""
        return service.get(id)

    hosts = set(allowed_hosts or os.environ.get("MEMES_MCP_HOSTS", "127.0.0.1:8766,localhost:8766").split(","))
    origins = set(allowed_origins or os.environ.get("MEMES_MCP_ORIGINS", "http://127.0.0.1:8766,http://localhost:8766").split(","))
    app = server.streamable_http_app(stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=sorted(hosts), allowed_origins=sorted(origins)))

    async def access(request, call_next):
        if request.headers.get("host") not in hosts or ("origin" in request.headers and request.headers["origin"] not in origins):
            return JSONResponse({"error": "forbidden_origin"}, status_code=403)
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    app.add_middleware(BaseHTTPMiddleware, dispatch=access)
    return app


def main():
    import uvicorn
    uvicorn.run(create_mcp_app(), host=os.environ.get("MEMES_MCP_BIND", "127.0.0.1"), port=8766, log_level="warning")


if __name__ == "__main__":
    main()
