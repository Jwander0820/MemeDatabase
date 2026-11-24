from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from contextlib import closing
from io import BytesIO
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from src.anime_memes.db import connect, init_db
from src.anime_memes.images import build_thumbnails
from src.anime_memes.renditions import (
    PRESETS,
    ensure_web_rendition,
    fit_size,
    render_webp,
    sha256_bytes,
)


class FitSizeTests(unittest.TestCase):
    def test_examples(self):
        web, thumb = PRESETS["web"], PRESETS["thumb"]
        self.assertEqual(fit_size(1920, 1080, web), (1280, 720))
        self.assertEqual(fit_size(1080, 1920, web), (720, 1280))
        self.assertEqual(fit_size(2000, 2000, web), (720, 720))
        self.assertEqual(fit_size(1920, 1080, thumb), (854, 480))
        self.assertEqual(fit_size(1080, 1920, thumb), (480, 854))

    def test_never_upscale(self):
        self.assertEqual(fit_size(640, 360, PRESETS["web"]), (640, 360))
        self.assertEqual(fit_size(640, 360, PRESETS["thumb"]), (640, 360))


class RenditionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.conn = connect(self.root / "t.sqlite3")
        self.addCleanup(self.conn.close)
        init_db(self.conn)

    def add_attachment(self, attachment_id, file_name, source):
        self.conn.execute("INSERT OR IGNORE INTO discord_messages (id, timestamp) VALUES ('m', '2024-01-01T00:00:00Z')")
        self.conn.execute(
            "INSERT INTO attachments (id, message_id, file_name, local_path) VALUES (?, 'm', ?, ?)",
            (attachment_id, file_name, str(source) if source else None),
        )
        self.conn.commit()

    def make_png(self, name="a.png", size=(1920, 1080), mode="RGB"):
        path = self.root / name
        Image.new(mode, size, (200, 30, 30, 255) if mode == "RGBA" else (200, 30, 30)).save(path)
        return path

    def test_render_strips_metadata(self):
        path = self.root / "meta.png"
        info = PngInfo()
        info.add_text("secret", "value")
        Image.new("RGB", (100, 100), "red").save(path, pnginfo=info)
        data, w, h = render_webp(path, "web")
        self.assertEqual((w, h), (100, 100))
        with Image.open(BytesIO(data)) as out:
            self.assertEqual(out.format, "WEBP")
            self.assertFalse(getattr(out, "text", {}))
            self.assertNotIn("exif", out.info)
            self.assertNotIn("icc_profile", out.info)

    def test_render_keeps_alpha(self):
        path = self.root / "alpha.png"
        Image.new("RGBA", (50, 50), (255, 0, 0, 100)).save(path)
        data, _, _ = render_webp(path, "thumb")
        with Image.open(BytesIO(data)) as out:
            self.assertEqual(out.mode, "RGBA")

    def test_render_applies_exif_orientation(self):
        path = self.root / "rot.jpg"
        image = Image.new("RGB", (200, 100), "blue")
        exif = Image.Exif()
        exif[0x0112] = 6  # 順時針 90 度
        image.save(path, exif=exif)
        _, w, h = render_webp(path, "web")
        self.assertEqual((w, h), (100, 200))

    def test_ensure_web_rendition_cache_and_force(self):
        source = self.make_png()
        self.add_attachment("a1", "a.png", source)
        web_dir = self.root / "web"
        path, digest = ensure_web_rendition(self.conn, "a1", web_dir=web_dir)
        self.assertTrue(path.is_file())
        self.assertEqual(digest, sha256_bytes(path.read_bytes()))
        with Image.open(path) as out:
            self.assertEqual(out.size, (1280, 720))
        row = self.conn.execute("SELECT web_path, web_sha256 FROM attachments WHERE id='a1'").fetchone()
        self.assertEqual(row["web_sha256"], digest)
        mtime = path.stat().st_mtime_ns
        path2, digest2 = ensure_web_rendition(self.conn, "a1", web_dir=web_dir)
        self.assertEqual((path2, digest2), (path, digest))
        self.assertEqual(path.stat().st_mtime_ns, mtime)
        path.unlink()
        ensure_web_rendition(self.conn, "a1", web_dir=web_dir, force=True)
        self.assertTrue(path.is_file())

    def test_ensure_web_rendition_errors(self):
        # 影片不產生大圖；GIF 改為支援（見 test_gif_*）。
        self.add_attachment("v1", "x.mp4", self.root / "x.mp4")
        with self.assertRaisesRegex(ValueError, "不支援的媒體格式"):
            ensure_web_rendition(self.conn, "v1", web_dir=self.root / "web")
        self.add_attachment("n1", "n.png", self.root / "missing.png")
        with self.assertRaisesRegex(ValueError, "缺少本地原圖"):
            ensure_web_rendition(self.conn, "n1", web_dir=self.root / "web")

    def make_gif(self, name, size=(1600, 900), frames=4):
        path = self.root / name
        images = [Image.new("RGB", size, color) for color in ("red", "green", "blue", "white")[:frames]]
        images[0].save(path, save_all=True, append_images=images[1:], duration=[80, 120, 160, 200][:frames], loop=0)
        return path

    def test_gif_web_rendition_is_animated_webp_and_thumbnail_is_first_frame(self):
        gif = self.make_gif("anim.gif")
        self.add_attachment("g1", "anim.gif", gif)
        path, _ = ensure_web_rendition(self.conn, "g1", web_dir=self.root / "web")
        with Image.open(path) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertEqual(image.n_frames, 4)
            self.assertEqual(image.size, (1280, 720))
            image.seek(2)
            image.load()  # WebP 的每幀時間要載入該幀後才會出現在 info
            self.assertEqual(image.info.get("duration"), 160)
        data, width, height = render_webp(gif, "thumb")
        with Image.open(BytesIO(data)) as thumb:
            self.assertEqual(getattr(thumb, "n_frames", 1), 1)
            self.assertEqual((width, height), (854, 480))
            red, green, blue = thumb.convert("RGB").getpixel((10, 10))
            self.assertTrue(red > 200 and green < 60 and blue < 60, "縮圖應是第一幀（紅色）")

    def test_gif_over_frame_limit_falls_back_to_static(self):
        gif = self.make_gif("long.gif", size=(320, 180))
        with patch("src.anime_memes.renditions.MAX_ANIMATION_FRAMES", 2):
            data, _, _ = render_webp(gif, "web", animated=True)
        with Image.open(BytesIO(data)) as image:
            self.assertEqual(getattr(image, "n_frames", 1), 1)

    def test_build_thumbnails_uses_thumb_preset(self):
        source = self.make_png()
        self.add_attachment("t1", "a.png", source)
        summary = build_thumbnails(self.conn, thumbnail_dir=self.root / "thumbs")
        self.assertEqual(summary["created"], 1)
        with Image.open(self.root / "thumbs" / "t1.webp") as out:
            self.assertLessEqual(out.width, 854)
            self.assertLessEqual(out.height, 480)


if __name__ == "__main__":
    unittest.main()
