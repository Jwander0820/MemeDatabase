"""管理端錯誤格式：與公開 API 相同的 {"error": {"code", "message"}}，且不外洩內部例外。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.anime_memes.web import create_app


class AdminErrorFormatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = create_app(db_path=Path(self.tmp.name) / "a.sqlite3")
        self.client = self.app.test_client()

    def assertError(self, response, status, code):
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, status)
        body = response.get_json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(body["error"]["code"], code)
        self.assertIsInstance(body["error"]["message"], str)
        return body["error"]["message"]

    def test_value_error_is_400(self):
        self.assertError(self.client.get("/api/entries/missing/tag-suggestions?status=bogus"), 404, "not_found")
        response = self.client.post("/api/publication/activate", json={"x": 1})
        self.assertEqual(self.assertError(response, 400, "invalid_request"), "啟用只接受 version")

    def test_abort_under_api_returns_json(self):
        self.assertError(self.client.get("/api/entries/missing"), 404, "not_found")
        self.assertError(self.client.get("/api/no-such-route"), 404, "not_found")
        self.assertError(self.client.post("/api/meta"), 405, "method_not_allowed")
        self.assertError(self.client.post("/api/meta", headers={"Origin": "http://evil.example"}), 403, "forbidden")

    def test_runtime_error_is_409(self):
        with patch("src.anime_memes.web_admin.decide_tag_suggestion", side_effect=RuntimeError("已被處理")):
            response = self.client.post("/api/tag-suggestions/1/decision", json={"decision": "accept"})
        self.assertEqual(self.assertError(response, 409, "conflict"), "已被處理")

    def test_unexpected_exception_is_generic_500(self):
        with patch("src.anime_memes.web_admin.metadata", side_effect=Exception("secret-internal-detail")):
            response = self.client.get("/api/meta")
        message = self.assertError(response, 500, "internal_error")
        self.assertEqual(message, "伺服器發生錯誤，請稍後再試")
        self.assertNotIn("secret-internal-detail", response.get_data(as_text=True))

    def test_testing_mode_still_propagates_exceptions(self):
        self.app.config["TESTING"] = True
        with patch("src.anime_memes.web_admin.metadata", side_effect=Exception("boom")):
            with self.assertRaises(Exception):
                self.client.get("/api/meta")

    def test_non_api_paths_keep_default_behaviour(self):
        response = self.client.get("/media/attachments/nope")
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(response.get_json(silent=True))
        self.assertIn("text/html", response.content_type)


class IntParamMessageTest(unittest.TestCase):
    """頁碼格式錯誤時回傳中文訊息，不外露 Python 的 int() 轉換錯誤。"""

    def test_管理與本機瀏覽與公開查詢(self):
        from src.anime_memes.public_data import int_param
        with self.assertRaisesRegex(ValueError, "^page 必須是整數$"):
            int_param({"page": "abc"}, "page", 1)
        self.assertEqual(int_param({"page": ""}, "page", 3), 3)
        self.assertEqual(int_param({}, "page", 3), 3)
        with tempfile.TemporaryDirectory() as tmp:
            client = create_app(db_path=Path(tmp) / "a.sqlite3").test_client()
            for path in ("/api/entries?page=abc", "/api/v1/entries?page_size=x"):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("必須是整數", response.get_json()["error"]["message"])
                    self.assertNotIn("invalid literal", response.get_data(as_text=True))
