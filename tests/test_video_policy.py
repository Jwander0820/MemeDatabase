"""影片不公開：單張勾選、批次公開與發布檢查都要擋下；GIF 則正常公開。"""
from contextlib import closing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from src.anime_memes.db import connect, init_db
from src.anime_memes.publishing import bulk_update_selection, check, export_release, snapshot, update_selection
from src.anime_memes.r2 import R2_ENV_VARS


class VideoPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "admin.sqlite3"
        self.enterContext(patch.dict(os.environ, {name: "" for name in R2_ENV_VARS}))
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        gif = self.root / "anim.gif"
        frames = [Image.new("RGB", (640, 360), c) for c in ("red", "blue")]
        frames[0].save(gif, save_all=True, append_images=frames[1:], duration=100, loop=0)
        video = self.root / "clip.mp4"
        video.write_bytes(b"not really a video")
        for n, path in (("gif", gif), ("video", video)):
            self.conn.execute("INSERT INTO discord_messages(id,timestamp,content) VALUES(?, '2026-11-24','x')", (n,))
            self.conn.execute("INSERT INTO attachments(id,message_id,local_path,file_name) VALUES(?,?,?,?)",
                              (n, n, str(path), path.name))
            self.conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,
                pairing_method,confidence,status) VALUES(?,?,?,'台詞','test',1,'ready')""", (f"meme_{n}", n, n))
        self.conn.commit()

    def test_single_selection_rejects_video(self):
        with self.assertRaisesRegex(ValueError, "影片"):
            update_selection(self.conn, "meme_video", {"publish_selected": True})
        update_selection(self.conn, "meme_video", {"publish_selected": False})  # 取消公開永遠允許

    def test_bulk_publish_skips_videos(self):
        result = bulk_update_selection(self.conn, selected=True, filters={"status": "ready"})
        self.assertEqual((result["matched"], result["skipped_videos"]), (1, ["meme_video"]))
        selected = dict(self.conn.execute("SELECT key, publish_selected FROM meme_entries"))
        self.assertEqual(selected, {"meme_gif": 1, "meme_video": 0})

    def test_check_blocks_video_and_gif_exports_with_first_frame_thumbnail(self):
        self.conn.execute("UPDATE meme_entries SET publish_selected=1, public_id=lower(hex(randomblob(16)))")
        self.conn.commit()
        with snapshot(self.db) as conn:
            report = check(conn)
        errors = {item["key"]: item["errors"] for item in report["items"]}
        self.assertTrue(any("影片" in e for e in errors["meme_video"]))
        self.assertEqual(errors["meme_gif"], [])
        update_selection(self.conn, "meme_video", {"publish_selected": False})
        result = export_release(self.db, self.root / "releases", store=None)
        self.assertTrue(result["exported"], result)
        media = list((self.root / "releases" / "media").glob("*.webp"))
        self.assertEqual(len(media), 1)
        with Image.open(media[0]) as thumb:
            self.assertEqual(getattr(thumb, "n_frames", 1), 1)


if __name__ == "__main__":
    unittest.main()
