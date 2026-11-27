"""deploy/hash-assets.sh：公開站靜態檔加內容雜湊並改寫 index.html。"""
import hashlib
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/hash-assets.sh"
PATTERN = re.compile(r'"/assets/([A-Za-z0-9_-]+)\.([0-9a-f]{12})\.(js|css|svg)"')


@unittest.skipUnless(shutil.which("sh") and shutil.which("sha256sum"), "需要 sh 與 sha256sum（Linux／容器）")
class HashAssetsTest(unittest.TestCase):
    def build(self) -> Path:
        gallery = Path(self.tmp.name) / "gallery"
        shutil.copytree(ROOT / "src/anime_memes/public_ui", gallery)
        for name in ("catalog.css", "favicon.svg"):
            shutil.copy(ROOT / "src/anime_memes/web_static" / name, gallery)
        subprocess.run(["sh", str(SCRIPT), str(gallery)], check=True)
        return gallery

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_index_references_hashed_copies_of_every_asset(self):
        original = (ROOT / "src/anime_memes/public_ui/index.html").read_text(encoding="utf-8")
        expected = set(re.findall(r'"/assets/([^"]+)"', original))
        gallery = self.build()
        refs = PATTERN.findall((gallery / "index.html").read_text(encoding="utf-8"))
        self.assertEqual({f"{base}.{ext}" for base, _, ext in refs}, expected)
        for base, digest, ext in refs:
            content = (gallery / f"{base}.{digest}.{ext}").read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest()[:12], digest)
            # 原檔名保留給私人管理頁與本機 Flask。
            self.assertEqual((gallery / f"{base}.{ext}").read_bytes(), content)

    def test_rerun_is_stable(self):
        gallery = self.build()
        before = sorted(p.name for p in gallery.iterdir())
        index = (gallery / "index.html").read_text(encoding="utf-8")
        subprocess.run(["sh", str(SCRIPT), str(gallery)], check=True)
        self.assertEqual(sorted(p.name for p in gallery.iterdir()), before)
        self.assertEqual((gallery / "index.html").read_text(encoding="utf-8"), index)


class NginxCacheConfigTest(unittest.TestCase):
    def test_hashed_assets_immutable_index_no_cache(self):
        conf = (ROOT / "deploy/nginx-public.conf.template").read_text(encoding="utf-8")
        hashed = conf.split('location ~ "^/assets/', 1)[1].split("\n    }", 1)[0]
        self.assertIn("immutable", hashed)
        self.assertIn("security-headers.conf", hashed)
        root = conf.split("location = / {", 1)[1].split("}", 1)[0]
        self.assertIn("Cache-Control no-cache", root)
        self.assertIn("hash-assets.sh", (ROOT / "deploy/Dockerfile.nginx").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
