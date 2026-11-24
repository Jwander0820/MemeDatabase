from __future__ import annotations

import csv
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from src.anime_memes.csv_text import encode_cell
from src.anime_memes.db import connect, init_db
from src.anime_memes.images import build_thumbnails, download_all_attachments, measure_image
from src.anime_memes.ingest import ingest_fixed_export, ocr_review_entries
from src.anime_memes.notion import export_notion_csv
from src.anime_memes.review import H, export_review_csv, import_review_corrections
from src.anime_memes.web import create_app


class AssessmentFixesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "test.sqlite3"
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        self.messages = []
        for number in range(1, 7):
            attachment_id = str(number)
            Image.new("RGB", (8, 8)).save(self.root / f"{attachment_id}.png")
            self.messages.append({"id": f"m{number}", "timestamp": f"2026-10-{number:02d}",
                                  "content": "作品\n角色\n測試台詞", "attachments": [
                                      {"id": attachment_id, "fileName": f"{attachment_id}.png",
                                       "url": "https://example.test/image"}]})
        self.fixed = self.root / "fixed.json"
        self.fixed.write_text(json.dumps({"messages": self.messages}), encoding="utf-8")
        ingest_fixed_export(self.conn, self.fixed, attachment_dir=self.root)
        self.app = create_app(db_path=self.db, attachment_dir=self.root, thumbnail_dir=self.root / "thumbs")
        self.client = self.app.test_client()

    def row(self, key="meme_1"):
        return dict(self.conn.execute("SELECT * FROM meme_entries WHERE key=?", (key,)).fetchone())

    def test_ocr_review_persists_auxiliary_fields_only(self):
        self.conn.execute("UPDATE meme_entries SET status='needs_review', confidence=.65, quote_text='人工台詞', notes='保留', anime_title='人工作品' WHERE key='meme_1'")
        self.conn.execute("UPDATE meme_entries SET status='ignored', confidence=.1 WHERE key='meme_2'")
        self.conn.commit()
        with patch("src.anime_memes.ingest.run_ocr", return_value=("人工台詞", "ok")):
            result = ocr_review_entries(self.conn, attachment_dir=self.root)
        row = self.row()
        self.assertEqual((row["ocr_text"], row["ocr_status"], row["status"]), ("人工台詞", "ok", "ready"))
        self.assertAlmostEqual(row["confidence"], .73)
        self.assertEqual((row["quote_text"], row["anime_title"], row["notes"]), ("人工台詞", "人工作品", "保留"))
        self.assertEqual(self.row("meme_2")["status"], "ignored")
        self.assertGreaterEqual(result["improved"], 1)

    def test_first_ingest_ocr_failure_has_valid_review_foreign_key(self):
        self.messages.append({"id": "m7", "timestamp": "2026-10-07", "content": "作品\n角色\n台詞",
                              "attachments": [{"id": "7", "fileName": "7.png"}]})
        Image.new("RGB", (8, 8)).save(self.root / "7.png")
        self.fixed.write_text(json.dumps({"messages": self.messages}), encoding="utf-8")
        with patch("src.anime_memes.ingest.run_ocr", return_value=("", "failed: test")):
            ingest_fixed_export(self.conn, self.fixed, attachment_dir=self.root, run_ocr=True)
        self.assertEqual(self.row("meme_7")["ocr_status"], "failed: test")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM review_items WHERE entry_key='meme_7' AND reason='ocr_skipped'").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_unsupported_ocr_result_persists(self):
        self.conn.execute("UPDATE attachments SET file_name='1.gif' WHERE id='1'")
        self.conn.execute("UPDATE meme_entries SET status='needs_review' WHERE key='meme_1'")
        self.conn.commit()
        with patch("src.anime_memes.ingest.run_ocr", side_effect=AssertionError("unsupported")):
            ocr_review_entries(self.conn, attachment_dir=self.root, limit=1)
        self.assertIn("unsupported media type (.gif)", self.row()["ocr_status"])

    def test_deleted_attachment_stays_deleted_after_reimport(self):
        self.assertEqual(self.client.delete("/api/entries/meme_1").status_code, 200)
        ingest_fixed_export(self.conn, self.fixed, attachment_dir=self.root)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM attachments WHERE id='1'").fetchone())
        self.assertIsNone(self.conn.execute("SELECT 1 FROM meme_entries WHERE key='meme_1'").fetchone())
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM discord_messages WHERE id='m1'").fetchone())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM meme_entries").fetchone()[0], 5)

    def test_random_pages_are_repeatable_and_disjoint(self):
        def keys(seed, page):
            data = self.client.get(f"/api/entries?status=all&sort=random&seed={seed}&page={page}&page_size=2").get_json()
            return [item["key"] for item in data["items"]]
        pages = [keys("one", page) for page in (1, 2, 3)]
        self.assertEqual(pages, [keys("one", page) for page in (1, 2, 3)])
        self.assertEqual(len(set(sum(pages, []))), 6)
        self.assertNotEqual(sum(pages, []), sum([keys("two", page) for page in (1, 2, 3)], []))

    def test_media_types_and_alternate_thumbnail_route(self):
        for suffix in (".html", ".SVG", ".svgz", ".xml", ".xhtml", ".unknown", "", ".png"):
            path = self.root / ("document" + suffix)
            path.write_text("<svg onload='alert(1)'></svg>", encoding="utf-8")
            self.conn.execute("UPDATE attachments SET local_path=?, thumbnail_path=? WHERE id='1'", (str(path), str(path)))
            self.conn.commit()
            for route in ("attachments", "thumbnails"):
                response = self.client.get(f"/media/{route}/1")
                self.addCleanup(response.close)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertIn("sandbox", response.headers["Content-Security-Policy"])
                self.assertNotIn(response.mimetype, ("text/html", "image/svg+xml"))
                if suffix != ".png":
                    self.assertTrue(response.headers["Content-Disposition"].startswith("attachment;"))
        for format, suffix, mime in (("PNG", ".png", "image/png"), ("GIF", ".gif", "image/gif")):
            path = self.root / ("valid" + suffix)
            Image.new("RGB", (8, 8)).save(path, format)
            self.conn.execute("UPDATE attachments SET local_path=? WHERE id='1'", (str(path),))
            self.conn.commit()
            response = self.client.get("/media/attachments/1", headers={"Range": "bytes=0-9"})
            self.addCleanup(response.close)
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.mimetype, mime)
            self.assertTrue(response.headers["Content-Disposition"].startswith("inline;"))

    def test_cross_origin_mutations_are_rejected_before_writing(self):
        for headers in ({"Origin": "https://attacker.test"}, {"Origin": "null"},
                        {"Origin": "http://localhost:8765"}, {"Origin": "http://127.0.0.1"},
                        {"Origin": "http://localhost http://attacker.test"}, {"Origin": "http://localhost/path"},
                        {"Referer": "https://attacker.test/form"}, {"Sec-Fetch-Site": "cross-site"},
                        {"Sec-Fetch-Site": "same-site"}):
            for method, route, kwargs in (("post", "/api/entries/meme_1/favorite", {}),
                                          ("patch", "/api/entries/meme_1", {"json": {"quote_text": "bad"}}),
                                          ("put", "/api/entries/meme_1/tags", {"json": {"tags": "bad"}}),
                                          ("delete", "/api/entries/meme_1", {}),
                                          ("post", "/api/entries/manual", {"data": {"quote_text": "bad"}})):
                self.assertEqual(getattr(self.client, method)(route, headers=headers, **kwargs).status_code, 403)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM entry_favorites").fetchone()[0], 0)
        self.assertNotEqual(self.row()["quote_text"], "bad")
        for base in ("http://localhost", "http://127.0.0.1:8765"):
            self.assertEqual(self.client.post("/api/entries/meme_1/favorite", base_url=base, headers={"Origin": base, "Sec-Fetch-Site": "same-origin"}).status_code, 200)
        self.assertEqual(self.client.post("/api/entries/meme_2/favorite").status_code, 200)
        self.assertEqual(self.client.post("/api/entries/meme_3/favorite", headers={"Referer": "http://localhost/editor"}).status_code, 200)

    def test_bomb_cached_download_and_thumbnail_batches_continue_and_skip_retry(self):
        Image.new("RGB", (1, 1)).save(self.root / "2.png")
        with patch.object(Image, "MAX_IMAGE_PIXELS", 10):
            self.assertEqual(measure_image(self.root / "1.png"), (None, None))
            summary = download_all_attachments(self.conn, attachment_dir=self.root)
            self.assertEqual(summary["failed"], 5)
            self.assertEqual(summary["cached"], 1)
            again = download_all_attachments(self.conn, attachment_dir=self.root)
            self.assertEqual((again["failed"], again["skipped"]), (0, 5))
            summary = build_thumbnails(self.conn, thumbnail_dir=self.root / "thumbs")
            self.assertEqual((summary["created"], summary["skipped"]), (1, 5))
        self.assertEqual(self.conn.execute("SELECT download_status FROM attachments WHERE id='1'").fetchone()[0], "invalid_image")
        self.conn.execute("UPDATE attachments SET download_status='pending'")
        self.conn.commit()
        with patch.object(Image, "MAX_IMAGE_PIXELS", 10):
            summary = build_thumbnails(self.conn, thumbnail_dir=self.root / "thumbs", force=True)
            self.assertEqual((summary["failed"], summary["created"]), (5, 1))
            self.assertEqual(build_thumbnails(self.conn, thumbnail_dir=self.root / "thumbs")["skipped"], 5)

    def test_bomb_fresh_download_and_force_recovery(self):
        def downloaded(url, path, **kwargs):
            Image.new("RGB", (8, 8)).save(path)
            return "downloaded", None
        (self.root / "1.png").unlink()
        with patch.object(Image, "MAX_IMAGE_PIXELS", 10), patch("src.anime_memes.images.download_attachment", side_effect=downloaded):
            self.assertEqual(download_all_attachments(self.conn, attachment_dir=self.root, limit=1)["failed"], 1)
        with patch("src.anime_memes.images.download_attachment", side_effect=downloaded):
            self.assertEqual(download_all_attachments(self.conn, attachment_dir=self.root, force=True, limit=1)["downloaded"], 1)

    def test_bomb_upload_cleans_files_and_normal_upload_works(self):
        payload = (self.root / "1.png").read_bytes()
        before = set(self.root.glob("manual_*"))
        with patch.object(Image, "MAX_IMAGE_PIXELS", 10):
            response = self.client.post("/api/entries/manual", data={"image": (BytesIO(payload), "image.png")}, headers={"Origin": "http://localhost"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(self.root.glob("manual_*")), before)
        self.assertEqual(list((self.root / "thumbs").glob("*")), [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM meme_entries").fetchone()[0], 6)
        response = self.client.post("/api/entries/manual", data={"image": (BytesIO(payload), "image.png")}, headers={"Origin": "http://localhost"})
        self.assertEqual(response.status_code, 201)

    def test_csv_formula_safety_and_unchanged_roundtrip(self):
        texts = ["=1+1", "+1", "-1", "@SUM(A1)", "\tplain", "\rplain", "\nplain", "  =1", "'=literal", "''literal", "繁體中文\n第二行"]
        for value in texts:
            self.conn.execute("UPDATE meme_entries SET status='needs_review', quote_text=?, anime_title='人工作品', notes='保留' WHERE key='meme_1'", (value,))
            self.conn.commit()
            for _ in range(2):
                path = export_review_csv(self.conn, self.root / "review.csv")
                with path.open(encoding="utf-8-sig", newline="") as handle:
                    row = next(csv.DictReader(handle))
                self.assertEqual(row[H["quote"]], encode_cell(value))
                import_review_corrections(self.conn, path)
                self.assertEqual(self.row()["quote_text"], value)
            path = export_notion_csv(self.conn, self.root / "notion.csv")
            with path.open(encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row[H["quote"]], encode_cell(value))

    def test_csv_fixed_input_and_unmarked_legacy_are_not_decoded(self):
        self.conn.execute("UPDATE meme_entries SET status='needs_review' WHERE key='meme_1'")
        self.conn.commit()
        path = export_review_csv(self.conn, self.root / "review.csv")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames
            rows = list(reader)
        rows[0][H["fixed_quote"]] = "'=人工修改"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        import_review_corrections(self.conn, path)
        self.assertEqual(self.row()["quote_text"], "'=人工修改")
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            csv.writer(handle).writerows([["key", "quote_text"], ["meme_1", "'=舊格式"]])
        import_review_corrections(self.conn, path)
        self.assertEqual(self.row()["quote_text"], "'=舊格式")


if __name__ == "__main__":
    unittest.main()
