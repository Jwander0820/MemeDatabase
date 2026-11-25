import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from src.anime_memes.db import connect, init_db
from src.anime_memes.publishing import export_release, update_selection
from src.anime_memes.r2 import R2_ENV_VARS
from src.anime_memes.release_bundle import build_bundle, install_bundle
from src.anime_memes.releases import validate


class ReleaseBundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.releases = self.base / "releases"
        self.enterContext(patch.dict(os.environ, {"MEMES_WEB_DIR": str(self.base / "web")}))
        self.enterContext(patch.dict(os.environ, {name: "" for name in R2_ENV_VARS}))
        conn = connect(self.base / "admin.sqlite3")
        self.addCleanup(conn.close)
        init_db(conn)
        thumb = self.base / "thumb.png"
        Image.new("RGB", (300, 200), "red").save(thumb)
        conn.execute("INSERT INTO discord_messages(id,timestamp,content) VALUES('0','2026-11-24','x')")
        conn.execute("INSERT INTO attachments(id,message_id,thumbnail_path,local_path,file_name) VALUES('0','0',?,NULL,NULL)", (str(thumb),))
        conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,
            pairing_method,confidence,status) VALUES('meme_0','0','0','台詞','test',1,'ready')""")
        conn.commit()
        update_selection(conn, "meme_0", {"publish_selected": True})
        result = export_release(self.base / "admin.sqlite3", self.releases, store=None)
        self.assertTrue(result["exported"])
        self.version = result["version"]
        self.manifest = validate(self.releases, self.version)
        self.bundle = self.base / "bundle.tar.gz"
        build_bundle(self.releases, self.version, self.bundle)
        self.target = self.base / "target"
        self.target.mkdir()

    def snapshot(self):
        return sorted(str(p.relative_to(self.target)) for p in self.target.rglob("*"))

    def make_bad(self, entries):
        """entries: list of (TarInfo, bytes|None)。"""
        path = self.base / "bad.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            for info, data in entries:
                tar.addfile(info, io.BytesIO(data) if data is not None else None)
        return path

    def reg(self, name, data=b"x"):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        return info, data

    def good_entries(self, version=None):
        version = version or self.version
        folder = self.releases / "versions" / self.version
        entries = [self.reg(f"versions/{version}/manifest.json", (folder / "manifest.json").read_bytes()),
                   self.reg(f"versions/{version}/public.sqlite3", (folder / "public.sqlite3").read_bytes())]
        for name in self.manifest["media"]:
            entries.append(self.reg(f"media/{name}", (self.releases / "media" / name).read_bytes()))
        return entries

    def assert_rejected(self, path):
        with self.assertRaises(ValueError):
            install_bundle(self.target, path)
        self.assertEqual(self.snapshot(), [])

    def test_bundle_contents(self):
        with tarfile.open(self.bundle) as tar:
            names = sorted(tar.getnames())
        expected = sorted([f"versions/{self.version}/manifest.json", f"versions/{self.version}/public.sqlite3"]
                          + ["media/" + n for n in self.manifest["media"]])
        self.assertEqual(names, expected)

    def test_install_without_activate(self):
        result = install_bundle(self.target, self.bundle)
        self.assertEqual(result["version"], self.version)
        self.assertFalse(result["activated"])
        self.assertEqual(result["media_added"], len(self.manifest["media"]))
        validate(self.target, self.version)
        self.assertFalse((self.target / "current.json").exists())
        self.assertEqual([p for p in self.target.iterdir() if p.name.startswith(".incoming-")], [])

    def test_install_with_activate(self):
        result = install_bundle(self.target, self.bundle, activate=True)
        self.assertTrue(result["activated"])
        data = json.loads((self.target / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(data, {"version": self.version})

    def test_duplicate_version_rejected(self):
        install_bundle(self.target, self.bundle)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            install_bundle(self.target, self.bundle)
        self.assertEqual(self.snapshot(), before)

    def test_existing_media_skipped(self):
        (self.target / "media").mkdir()
        name = next(iter(self.manifest["media"]))
        (self.target / "media" / name).write_bytes((self.releases / "media" / name).read_bytes())
        result = install_bundle(self.target, self.bundle)
        self.assertEqual(result["media_existing"], 1)
        self.assertEqual(result["media_added"], len(self.manifest["media"]) - 1)

    def test_existing_media_mismatch_rejected(self):
        (self.target / "media").mkdir()
        name = next(iter(self.manifest["media"]))
        (self.target / "media" / name).write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            install_bundle(self.target, self.bundle)
        self.assertFalse((self.target / "versions" / self.version).exists())

    def test_traversal_rejected(self):
        self.assert_rejected(self.make_bad(self.good_entries() + [self.reg("../x")]))

    def test_absolute_path_rejected(self):
        self.assert_rejected(self.make_bad(self.good_entries() + [self.reg("/etc/x")]))

    def test_backslash_rejected(self):
        self.assert_rejected(self.make_bad(self.good_entries() + [self.reg("versions\\x")]))

    def test_symlink_rejected(self):
        link = tarfile.TarInfo("media/" + "a" * 64 + ".webp")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        self.assert_rejected(self.make_bad(self.good_entries() + [(link, None)]))

    def test_non_whitelisted_rejected(self):
        for name in ("reports/x.json", "current.json"):
            with self.subTest(name=name):
                self.assert_rejected(self.make_bad(self.good_entries() + [self.reg(name)]))

    def test_two_versions_rejected(self):
        self.assert_rejected(self.make_bad(self.good_entries() + self.good_entries("other")))

    def test_missing_database_rejected(self):
        entries = [e for e in self.good_entries() if not e[0].name.endswith("public.sqlite3")]
        self.assert_rejected(self.make_bad(entries))

    def test_duplicate_member_rejected(self):
        self.assert_rejected(self.make_bad(self.good_entries() + self.good_entries()[:1]))

    def test_media_hash_mismatch_rejected(self):
        entries = []
        for info, data in self.good_entries():
            if info.name.startswith("media/"):
                data = b"tampered"
                info = tarfile.TarInfo(info.name)
                info.size = len(data)
            entries.append((info, data))
        path = self.make_bad(entries)
        with self.assertRaises(ValueError):
            install_bundle(self.target, path)
        self.assertFalse((self.target / "versions" / self.version).exists())
        self.assertFalse((self.target / "current.json").exists())
        self.assertEqual([p for p in self.target.iterdir() if p.name.startswith(".incoming-")], [])

    def test_database_tampered_rolls_back_version(self):
        entries = []
        for info, data in self.good_entries():
            if info.name.endswith("public.sqlite3"):
                data = data + b"junk"
                info = tarfile.TarInfo(info.name)
                info.size = len(data)
            entries.append((info, data))
        with self.assertRaises(ValueError):
            install_bundle(self.target, self.make_bad(entries))
        self.assertFalse((self.target / "versions" / self.version).exists())


if __name__ == "__main__":
    unittest.main()
