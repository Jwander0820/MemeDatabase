import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.anime_memes.web import create_app

ROOT = Path(__file__).resolve().parents[1]
PAGES = [ROOT / "src/anime_memes/public_ui/index.html", ROOT / "src/anime_memes/web_templates/index.html"]


class AdminHostTest(unittest.TestCase):
    """私人管理端只接受本機 Host，擋 DNS rebinding。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = create_app(db_path=Path(self.tmp.name) / "a.sqlite3").test_client()

    def get(self, path, host):
        return self.client.get(path, headers={"Host": host})

    def test_本機名稱允許(self):
        for host in ("localhost", "localhost:3636", "127.0.0.1:3648", "[::1]:3636", "LOCALHOST"):
            with self.subTest(host=host):
                self.assertEqual(self.get("/api/meta", host).status_code, 200)

    def test_其他網域拒絕且讀取也擋(self):
        for host in ("evil.example", "evil.example:3636", "127.0.0.1.evil.example", "localhost.evil.example", ""):
            with self.subTest(host=host):
                self.assertEqual(self.get("/api/meta", host).status_code, 403)
                self.assertEqual(self.get("/api/v1/entries", host).status_code, 403)

    def test_寫入也擋(self):
        response = self.client.post("/api/entries/x/favorite",
                                    headers={"Host": "evil.example:3636", "Origin": "http://evil.example:3636"})
        self.assertEqual(response.status_code, 403)

    def test_健康檢查不受限(self):
        # 容器經 Nginx 轉發時 Host 是服務名稱。
        self.assertEqual(self.get("/health/live", "private-api:8765").status_code, 200)
        self.assertEqual(self.get("/health/ready", "private-api:8765").status_code, 200)

    def test_可用環境變數追加主機(self):
        with patch.dict(os.environ, {"MEMES_ADMIN_HOSTS": "nas.local, Admin.LAN"}):
            self.assertEqual(self.get("/api/meta", "nas.local:3636").status_code, 200)
            self.assertEqual(self.get("/api/meta", "admin.lan").status_code, 200)
            self.assertEqual(self.get("/api/meta", "evil.example").status_code, 403)


class CspCompatibleMarkupTest(unittest.TestCase):
    """公開頁的 CSP 不允許 inline script 與 style 屬性；頁面不可再加回來。"""

    def test_沒有_inline_script_與事件屬性(self):
        for page in PAGES:
            html = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html), [])
                self.assertEqual(re.findall(r"\son[a-z]+\s*=", html), [])

    def test_沒有_style_屬性(self):
        for page in PAGES:
            with self.subTest(page=page.name):
                self.assertEqual(re.findall(r"\sstyle\s*=", page.read_text(encoding="utf-8")), [])

    def test_主題腳本在樣式前同步載入(self):
        for page in PAGES:
            html = page.read_text(encoding="utf-8")
            with self.subTest(page=page.name):
                tag = '<script src="/assets/theme-init.js"></script>'
                self.assertIn(tag, html)
                self.assertLess(html.index(tag), html.index('rel="stylesheet"'))


if __name__ == "__main__":
    unittest.main()
