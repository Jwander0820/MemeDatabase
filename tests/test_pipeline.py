from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from src.anime_memes.db import connect, init_db
from src.anime_memes.exporter import incremental_after, save_sync_state
from src.anime_memes.images import build_thumbnails, download_all_attachments
from src.anime_memes.ingest import ingest_fixed_export, ocr_review_entries
from src.anime_memes.notion import export_notion_csv
from src.anime_memes.ocr import extract_paddle_text, run_ocr
from src.anime_memes.review import H, export_review_csv, import_review_corrections
from src.anime_memes.tags import import_llm_suggestions, import_manual_tags
from src.anime_memes.web import create_app


def message(message_id, timestamp, content, author_id="u1", attachments=None):
    return {
        "id": message_id,
        "type": "Default",
        "timestamp": timestamp,
        "timestampEdited": None,
        "callEndedTimestamp": None,
        "isPinned": False,
        "content": content,
        "author": {
            "id": author_id,
            "name": f"name_{author_id}",
            "discriminator": "0000",
            "isBot": False,
        },
        "attachments": attachments or [],
    }


def attachment(attachment_id, file_name="image.png"):
    return {
        "id": attachment_id,
        "url": f"https://cdn.example.test/{attachment_id}/{file_name}",
        "fileName": file_name,
        "fileSizeBytes": 123,
    }


class PipelineTest(unittest.TestCase):
    def test_durable_sync_cursor_advances_with_overlap_and_never_regresses(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "memes.sqlite3")
            try:
                init_db(conn)
                run_id = conn.execute(
                    "INSERT INTO export_runs(channel_id, status) VALUES('channel-1', 'success')"
                ).lastrowid
                save_sync_state(
                    conn,
                    channel_id="channel-1",
                    export_run_id=int(run_id),
                    after_value="2026-07-10",
                    covered_through="2026-07-12",
                    last_message_timestamp="2026-07-11T20:00:00+08:00",
                    last_message_id="m2",
                )
                self.assertEqual(incremental_after(conn, "channel-1"), "2026-07-11")

                save_sync_state(
                    conn,
                    channel_id="channel-1",
                    export_run_id=int(run_id),
                    after_value="2026-07-01",
                    covered_through="2026-07-05",
                    last_message_timestamp=None,
                    last_message_id=None,
                )
                state = conn.execute(
                    "SELECT covered_through, last_message_timestamp, last_message_id FROM discord_sync_state WHERE channel_id='channel-1'"
                ).fetchone()
                self.assertEqual(tuple(state), ("2026-07-12", "2026-07-11T20:00:00+08:00", "m2"))
            finally:
                conn.close()

    def test_ingest_pairing_idempotency_and_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixed = root / "sample.fixed.json"
            rules = root / "tags.rules.json"
            db_path = root / "memes.sqlite3"
            manual_csv = root / "manual_tags.csv"
            llm_jsonl = root / "llm.jsonl"
            notion_csv = root / "notion.csv"
            rules.write_text(
                json.dumps(
                    {"rules": [{"tag": "嗆人", "keywords": ["笨蛋", "噁心"]}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            fixed.write_text(
                json.dumps(
                    {
                        "dateRange": {"after": "2026-07-01", "before": "2026-07-02"},
                        "exportedAt": "2026-07-02T00:00:00+08:00",
                        "messages": [
                            message(
                                "m1",
                                "2026-07-01T00:00:00+08:00",
                                "芙莉蓮\n費倫\n說得也是 畢竟你是笨蛋",
                                attachments=[attachment("a1")],
                            ),
                            message("m2", "2026-07-01T00:01:00+08:00", "間諜教室\n愛爾娜\n這個男人是怪物"),
                            message("m3", "2026-07-01T00:01:05+08:00", "", attachments=[attachment("a2")]),
                            message("m4", "2026-07-01T00:02:00+08:00", "", attachments=[attachment("a3")]),
                            message("m5", "2026-07-01T00:02:04+08:00", "SSSS\n寶多六花\n噁心"),
                        ],
                        "messageCount": 5,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            conn = connect(db_path)
            try:
                first = ingest_fixed_export(conn, fixed, tag_rules_path=rules)
                second = ingest_fixed_export(conn, fixed, tag_rules_path=rules)
                self.assertEqual(first["messages"], 5)
                run = conn.execute(
                    "SELECT first_message_timestamp, first_message_id, last_message_timestamp, last_message_id FROM export_runs WHERE id=?",
                    (first["export_run_id"],),
                ).fetchone()
                self.assertEqual(
                    tuple(run),
                    ("2026-07-01T00:00:00+08:00", "m1", "2026-07-01T00:02:04+08:00", "m5"),
                )
                self.assertEqual(second["messages"], 5)
                self.assertEqual(count(conn, "discord_messages"), 5)
                self.assertEqual(count(conn, "attachments"), 3)
                self.assertEqual(count(conn, "meme_entries"), 3)
                self.assertEqual(count(conn, "review_items"), 0)
                self.assertGreaterEqual(count(conn, "entry_tags"), 2)
                methods = {
                    row["key"]: row["pairing_method"]
                    for row in conn.execute("SELECT key, pairing_method FROM meme_entries")
                }
                self.assertEqual(methods["meme_a1"], "same_message")
                self.assertEqual(methods["meme_a2"], "nearby_previous")
                self.assertEqual(methods["meme_a3"], "nearby_next")

                conn.execute(
                    "UPDATE meme_entries SET quote_text='人工修正', status='ignored', ocr_status='ok:paddleocr' WHERE key='meme_a1'"
                )
                third = ingest_fixed_export(conn, fixed, tag_rules_path=rules)
                preserved = conn.execute(
                    "SELECT quote_text, status, ocr_status FROM meme_entries WHERE key='meme_a1'"
                ).fetchone()
                self.assertEqual(tuple(preserved), ("人工修正", "ignored", "ok:paddleocr"))
                self.assertEqual(third["new_messages"], 0)
                self.assertEqual(third["changed_messages"], 0)

                edited = json.loads(fixed.read_text(encoding="utf-8"))
                edited["messages"][0]["content"] = "Discord 編輯後內容"
                edited["messages"][0]["timestampEdited"] = "2026-07-02T01:00:00+08:00"
                fixed.write_text(json.dumps(edited, ensure_ascii=False), encoding="utf-8")
                fourth = ingest_fixed_export(conn, fixed, tag_rules_path=rules)
                self.assertEqual(fourth["changed_messages"], 1)
                self.assertEqual(
                    conn.execute("SELECT content FROM discord_messages WHERE id='m1'").fetchone()[0],
                    "Discord 編輯後內容",
                )
                preserved = conn.execute(
                    "SELECT quote_text, status, ocr_status FROM meme_entries WHERE key='meme_a1'"
                ).fetchone()
                self.assertEqual(tuple(preserved), ("人工修正", "ignored", "ok:paddleocr"))

                manual_csv.write_text("key,tags,notes\nmeme_a1,\"人工,嗆人\",ok\n", encoding="utf-8")
                imported_manual = import_manual_tags(conn, manual_csv)
                self.assertEqual(imported_manual, 2)

                llm_jsonl.write_text(
                    '{"key":"meme_a2","suggested_tags":["本地LLM"],"confidence":0.7,"reason":"test"}\n',
                    encoding="utf-8",
                )
                imported_llm, errors = import_llm_suggestions(conn, llm_jsonl)
                self.assertEqual((imported_llm, errors), (1, 0))

                output = export_notion_csv(conn, notion_csv)
            finally:
                conn.close()

            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["Key"], "meme_a1")

    def test_ocr_missing_dependency_is_non_fatal(self):
        text, status = run_ocr("missing.png", engine="paddle")
        self.assertEqual(text, "")
        self.assertTrue(status == "ok:paddleocr" or status.startswith("skipped:"))

    def test_extract_paddleocr_v3_result(self):
        result = [{"rec_texts": ["Hello OCR 123"], "rec_scores": [0.99]}]
        self.assertEqual(extract_paddle_text(result), ["Hello OCR 123"])

    def test_ocr_review_skips_gif_and_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixed = root / "sample.fixed.json"
            db_path = root / "memes.sqlite3"
            fixed.write_text(
                json.dumps(
                    {
                        "dateRange": {"after": None, "before": None},
                        "exportedAt": "2026-07-02T00:00:00+08:00",
                        "messages": [
                            message(
                                "m1",
                                "2026-07-01T00:00:00+08:00",
                                "",
                                attachments=[attachment("gif1", "a.gif")],
                            ),
                            message(
                                "m2",
                                "2026-07-01T00:00:10+08:00",
                                "",
                                attachments=[attachment("mp4_1", "a.mp4")],
                            ),
                        ],
                        "messageCount": 2,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            conn = connect(db_path)
            try:
                ingest_fixed_export(conn, fixed)
                summary = ocr_review_entries(conn, limit=10, download_missing=True)
                self.assertEqual(summary["skipped_unsupported"], 2)
                self.assertEqual(summary["processed"], 0)
            finally:
                conn.close()

    def test_review_csv_export_and_correction_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixed = root / "sample.fixed.json"
            db_path = root / "memes.sqlite3"
            review_csv = root / "review.csv"
            fixed.write_text(
                json.dumps(
                    {
                        "dateRange": {"after": None, "before": None},
                        "exportedAt": "2026-07-02T00:00:00+08:00",
                        "messages": [
                            message(
                                "m1",
                                "2026-07-01T00:00:00+08:00",
                                "",
                                attachments=[attachment("a1")],
                            )
                        ],
                        "messageCount": 1,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            conn = connect(db_path)
            try:
                ingest_fixed_export(conn, fixed)
                output = export_review_csv(conn, review_csv)
                with output.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                    self.assertIn(H["work_title"], rows[0])
                    rows[0][H["work_title"]] = "\u6e2c\u8a66\u4f5c\u54c1"
                    rows[0][H["speaker"]] = "\u89d2\u8272"
                    rows[0][H["quote"]] = "\u53f0\u8a5e"
                    rows[0][H["fixed_tags"]] = "\u55c6\u4eba\uff0c\u71ac\u591c"
                with output.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)

                summary = import_review_corrections(conn, output, mark_ready=True)
                self.assertEqual(summary["updated"], 1)
                self.assertEqual(summary["tags_added"], 2)
                entry = conn.execute("SELECT * FROM meme_entries WHERE key = 'meme_a1'").fetchone()
                self.assertEqual(entry["anime_title"], "\u6e2c\u8a66\u4f5c\u54c1")
                self.assertEqual(entry["status"], "ready")
            finally:
                conn.close()

    def test_web_api_search_edit_tags_and_favorite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixed = root / "sample.fixed.json"
            db_path = root / "memes.sqlite3"
            fixed.write_text(
                json.dumps(
                    {
                        "dateRange": {"after": None, "before": None},
                        "exportedAt": "2026-07-02T00:00:00+08:00",
                        "messages": [
                            message(
                                "m1",
                                "2026-07-01T00:00:00+08:00",
                                "Test Anime\nHero\nNeed coffee",
                                attachments=[attachment("a1")],
                            )
                        ],
                        "messageCount": 1,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            conn = connect(db_path)
            try:
                ingest_fixed_export(conn, fixed)
            finally:
                conn.close()

            app = create_app(db_path=db_path, attachment_dir=root / "attachments", thumbnail_dir=root / "thumbs")
            client = app.test_client()
            response = client.get("/api/entries?q=coffee")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["total"], 1)

            patch_response = client.patch(
                "/api/entries/meme_a1",
                json={"anime_title": "Better Anime", "speaker": "Lead", "quote_text": "Fixed quote", "status": "ready"},
            )
            self.assertEqual(patch_response.status_code, 200)
            tag_response = client.put("/api/entries/meme_a1/tags", json={"tags": "收藏, 咖啡"})
            self.assertEqual(tag_response.status_code, 200)
            fav_response = client.post("/api/entries/meme_a1/favorite")
            self.assertEqual(fav_response.get_json()["favorite"], True)

            filtered = client.get("/api/entries?favorite=1&tag=咖啡").get_json()
            self.assertEqual(filtered["total"], 1)

    def test_web_api_manual_upload_creates_local_entry_and_thumbnail(self):
        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image

            root = Path(tmp)
            db_path = root / "memes.sqlite3"
            attachment_dir = root / "attachments"
            thumbnail_dir = root / "thumbs"
            image_bytes = BytesIO()
            Image.new("RGB", (640, 360), "#b3263a").save(image_bytes, "PNG")
            image_bytes.seek(0)

            app = create_app(
                db_path=db_path,
                attachment_dir=attachment_dir,
                thumbnail_dir=thumbnail_dir,
            )
            client = app.test_client()
            response = client.post(
                "/api/entries/manual",
                data={
                    "image": (image_bytes, "manual meme.png"),
                    "anime_title": "手動作品",
                    "speaker": "手動角色",
                    "quote_text": "手動新增台詞",
                    "tags": "收藏, 手動",
                    "status": "ready",
                    "notes": "UI upload test",
                },
                content_type="multipart/form-data",
            )
            self.assertEqual(response.status_code, 201)
            item = response.get_json()
            self.assertTrue(item["key"].startswith("meme_manual_"))
            self.assertEqual(item["pairing_method"], "manual_upload")
            self.assertEqual(item["anime_title"], "手動作品")
            self.assertCountEqual(item["tags"], ["手動", "收藏"])
            self.assertTrue(Path(item["local_path"]).exists())
            self.assertTrue(Path(item["thumbnail_path"]).exists())
            self.assertEqual((item["image_width"], item["image_height"]), (640, 360))

            conn = connect(db_path)
            try:
                message = conn.execute(
                    "SELECT type, export_run_id FROM discord_messages WHERE id = ?",
                    (item["source_message_id"],),
                ).fetchone()
                self.assertEqual(tuple(message), ("Manual", None))
                self.assertEqual(count(conn, "meme_entries"), 1)
                self.assertEqual(count(conn, "attachments"), 1)
            finally:
                conn.close()

            listed = client.get("/api/entries?status=ready&q=手動新增台詞").get_json()
            self.assertEqual(listed["total"], 1)

    def test_web_api_manual_upload_rejects_non_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "memes.sqlite3"
            app = create_app(
                db_path=db_path,
                attachment_dir=root / "attachments",
                thumbnail_dir=root / "thumbs",
            )
            response = app.test_client().post(
                "/api/entries/manual",
                data={"image": (BytesIO(b"not an image"), "fake.png")},
                content_type="multipart/form-data",
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("無法讀取圖片", response.get_json()["error"])
            conn = connect(db_path)
            try:
                init_db(conn)
                self.assertEqual(count(conn, "meme_entries"), 0)
            finally:
                conn.close()

    def test_web_api_permanent_delete_removes_unshared_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixed = root / "sample.fixed.json"
            db_path = root / "memes.sqlite3"
            attachment_dir = root / "attachments"
            thumbnail_dir = root / "thumbs"
            attachment_dir.mkdir()
            thumbnail_dir.mkdir()
            original = attachment_dir / "a1.png"
            thumbnail = thumbnail_dir / "a1.webp"
            original.write_bytes(b"original image bytes")
            thumbnail.write_bytes(b"thumbnail bytes")
            fixed.write_text(
                json.dumps(
                    {
                        "dateRange": {"after": None, "before": None},
                        "exportedAt": "2026-07-02T00:00:00+08:00",
                        "messages": [
                            message(
                                "m1",
                                "2026-07-01T00:00:00+08:00",
                                "Delete Anime\nCleaner\nRemove me",
                                attachments=[attachment("a1")],
                            )
                        ],
                        "messageCount": 1,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            conn = connect(db_path)
            try:
                ingest_fixed_export(conn, fixed)
                conn.execute(
                    """
                    UPDATE attachments
                    SET local_path = ?, thumbnail_path = ?, download_status = 'downloaded'
                    WHERE id = 'a1'
                    """,
                    (str(original), str(thumbnail)),
                )
                conn.execute("INSERT OR IGNORE INTO tags(name, source) VALUES('manual-tag', 'manual')")
                conn.execute(
                    """
                    INSERT INTO entry_tags(entry_key, tag_name, source, reason)
                    VALUES('meme_a1', 'manual-tag', 'manual', 'test')
                    """
                )
                conn.execute("INSERT INTO entry_favorites(entry_key) VALUES('meme_a1')")
                conn.execute(
                    "INSERT INTO review_items(entry_key, attachment_id, reason) VALUES('meme_a1', 'a1', 'entry review')"
                )
                conn.execute("INSERT INTO review_items(attachment_id, reason) VALUES('a1', 'attachment review')")
                conn.commit()
            finally:
                conn.close()

            app = create_app(db_path=db_path, attachment_dir=attachment_dir, thumbnail_dir=thumbnail_dir)
            client = app.test_client()
            response = client.delete("/api/entries/meme_a1")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data["removed_attachment"])
            self.assertEqual(len(data["file_errors"]), 0)
            self.assertFalse(original.exists())
            self.assertFalse(thumbnail.exists())

            conn = connect(db_path)
            try:
                self.assertEqual(count(conn, "meme_entries"), 0)
                self.assertEqual(count(conn, "attachments"), 0)
                self.assertEqual(count(conn, "entry_tags"), 0)
                self.assertEqual(count(conn, "entry_favorites"), 0)
                self.assertEqual(count(conn, "review_items"), 0)
                self.assertEqual(count(conn, "discord_messages"), 1)
            finally:
                conn.close()

    def test_download_and_thumbnail_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image

            root = Path(tmp)
            fixed = root / "sample.fixed.json"
            db_path = root / "memes.sqlite3"
            attachment_dir = root / "attachments"
            thumbnail_dir = root / "thumbs"
            source = root / "source.png"
            Image.new("RGB", (640, 360), "white").save(source)
            fixed.write_text(
                json.dumps(
                    {
                        "dateRange": {"after": None, "before": None},
                        "exportedAt": "2026-07-02T00:00:00+08:00",
                        "messages": [
                            message(
                                "m1",
                                "2026-07-01T00:00:00+08:00",
                                "A\nB\nC",
                                attachments=[attachment("a1", "image.png")],
                            )
                        ],
                        "messageCount": 1,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            conn = connect(db_path)
            try:
                ingest_fixed_export(conn, fixed, attachment_dir=attachment_dir)

                def fake_download(_url, output_path, timeout=30):
                    Path(output_path).write_bytes(source.read_bytes())
                    return "downloaded", None

                with patch("src.anime_memes.images.download_attachment", fake_download):
                    summary = download_all_attachments(conn, attachment_dir=attachment_dir, retry_failed=True)
                self.assertEqual(summary["downloaded"], 1)
                thumb_summary = build_thumbnails(conn, thumbnail_dir=thumbnail_dir)
                self.assertEqual(thumb_summary["created"], 1)
                row = conn.execute("SELECT local_path, thumbnail_path, image_width, image_height FROM attachments WHERE id='a1'").fetchone()
                self.assertTrue(Path(row["local_path"]).exists())
                self.assertTrue(Path(row["thumbnail_path"]).exists())
                self.assertEqual((row["image_width"], row["image_height"]), (640, 360))
            finally:
                conn.close()


def count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
