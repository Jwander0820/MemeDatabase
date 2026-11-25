"""發布 API 路由層：參數驗證與錯誤回應格式（流程本身由 test_publication*.py 覆蓋）。"""
import tempfile
import unittest
from pathlib import Path

from src.anime_memes.web import create_app


class WebPublicationRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.client = create_app(db_path=root / "a.sqlite3", release_root=root / "releases").test_client()

    def assertInvalid(self, response, message=None):
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 400)
        error = response.get_json()["error"]
        self.assertEqual(error["code"], "invalid_request")
        if message:
            self.assertIn(message, error["message"])

    def test_不存在的條目回_404(self):
        for method in (self.client.get, self.client.patch):
            response = method("/api/entries/nope/publication", json={"publish_selected": True})
            self.addCleanup(response.close)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.get_json()["error"]["code"], "not_found")

    def test_bulk_缺少_publish_selected(self):
        self.assertInvalid(self.client.post("/api/publication/bulk", json={"keys": ["a"]}), "publish_selected")

    def test_bulk_未知欄位與非物件(self):
        self.assertInvalid(self.client.post("/api/publication/bulk", json={"publish_selected": True, "foo": 1}), "foo")
        self.assertInvalid(self.client.post("/api/publication/bulk", json=[1]), "JSON 物件")
        self.assertInvalid(self.client.post("/api/publication/bulk", data="not json"), "JSON 物件")

    def test_export_帶參數被拒絕(self):
        self.assertInvalid(self.client.post("/api/publication/export", json={"path": "/tmp/x"}), "匯出")

    def test_activate_只接受_version(self):
        for body in [{}, {"version": "v1", "extra": 1}, {"other": 1}, [1]]:
            with self.subTest(body=body):
                self.assertInvalid(self.client.post("/api/publication/activate", json=body), "version")

    def test_activate_不存在的發布包被拒絕(self):
        response = self.client.post("/api/publication/activate", json={"version": "no-such-version"})
        self.addCleanup(response.close)
        self.assertIn(response.status_code, (400, 422))
        self.assertIn("code", response.get_json()["error"])
        self.assertFalse((Path(self.tmp.name) / "releases" / "current.json").exists())

    def test_purge_參數白名單(self):
        self.assertInvalid(self.client.post("/api/publication/purge", json={"path": "/"}), "dry_run")
        self.assertInvalid(self.client.post("/api/publication/purge", json=[]), "dry_run")


if __name__ == "__main__":
    unittest.main()
