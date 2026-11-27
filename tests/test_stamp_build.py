"""deploy/stamp-build.sh：把部署版號寫進公開站 index.html。"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/stamp-build.sh"
SHA = "0123456789abcdef0123456789abcdef01234567"


@unittest.skipUnless(shutil.which("sh"), "需要 sh（Linux／容器）")
class StampBuildTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.index = Path(tmp.name) / "index.html"
        shutil.copy(ROOT / "src/anime_memes/public_ui/index.html", self.index)

    def stamp(self, *args):
        return subprocess.run(["sh", str(SCRIPT), str(self.index), *args], capture_output=True, text=True)

    def test_writes_meta_and_footer(self):
        self.assertEqual(self.stamp(SHA, "2026-11-27T09:30:00Z").returncode, 0)
        html = self.index.read_text(encoding="utf-8")
        self.assertIn(f'<meta name="build-version" content="{SHA} 2026-11-27T09:30:00Z">', html)
        self.assertIn('<span class="build-version" title="建置於 2026-11-27T09:30:00Z">0123456</span></footer>', html)

    def test_no_revision_leaves_placeholders_empty(self):
        before = self.index.read_text(encoding="utf-8")
        self.assertEqual(self.stamp("", "").returncode, 0)
        self.assertEqual(self.index.read_text(encoding="utf-8"), before)

    def test_rejects_malformed_values(self):
        for args in (("not-a-sha", "2026-11-27T09:30:00Z"), (SHA, "yesterday"), (SHA, "")):
            with self.subTest(args=args):
                self.assertNotEqual(self.stamp(*args).returncode, 0)

    def test_dockerfiles_and_workflow_pass_build_args(self):
        self.assertIn("stamp-build.sh", (ROOT / "deploy/Dockerfile.nginx").read_text(encoding="utf-8"))
        self.assertIn("MEMEDB_BUILD_REVISION=$BUILD_REVISION", (ROOT / "Dockerfile").read_text(encoding="utf-8"))
        workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
        self.assertIn('--build-arg "BUILD_REVISION=$GITHUB_SHA"', workflow)
