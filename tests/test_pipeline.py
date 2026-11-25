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
from src.anime_memes.tagging import export_tagging_jobs, import_tagging_results
from src.anime_memes.tags import import_llm_suggestions, import_manual_tags, load_taxonomy, validate_taxonomy
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
                    '{"key":"meme_a2","suggested_tags":["嗆人"],"confidence":0.7,"reason":"test"}\n',
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

    def test_taxonomy_v2_validation_and_core_labels(self):
        taxonomy = load_taxonomy()
        self.assertEqual(taxonomy["version"], 2)
        labels = {tag["label"] for tag in taxonomy["tags"]}
        self.assertTrue({"嗆人", "加班", "熬夜", "變態", "梗圖"}.issubset(labels))

        broken = json.loads(json.dumps(taxonomy, ensure_ascii=False))
        broken["tags"][1]["id"] = broken["tags"][0]["id"]
        with self.assertRaisesRegex(ValueError, "duplicate tag id"):
            validate_taxonomy(broken)

    def test_legacy_tag_schema_migrates_without_losing_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.sqlite3"
            conn = connect(db_path)
            try:
                init_db(conn)
                conn.executescript(
                    """
                    DROP TABLE tag_suggestions;
                    DROP TABLE tagging_run_entries;
                    DROP TABLE tagging_runs;
                    DROP TABLE entry_tags;
                    DROP TABLE tags;
                    CREATE TABLE tags (
                        name TEXT PRIMARY KEY,
                        source TEXT NOT NULL DEFAULT 'manual',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE entry_tags (
                        entry_key TEXT NOT NULL,
                        tag_name TEXT NOT NULL,
                        source TEXT NOT NULL,
                        confidence REAL,
                        reason TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (entry_key, tag_name, source),
                        FOREIGN KEY (entry_key) REFERENCES meme_entries(key),
                        FOREIGN KEY (tag_name) REFERENCES tags(name)
                    );
                    INSERT INTO discord_messages(id, timestamp, content) VALUES('m1', '2026-07-13', '');
                    INSERT INTO attachments(id, message_id, file_name) VALUES('a1', 'm1', 'x.png');
                    INSERT INTO meme_entries(
                        key, attachment_id, source_message_id, quote_text,
                        pairing_method, confidence, status
                    ) VALUES('meme_a1', 'a1', 'm1', '', 'test', 1, 'ready');
                    INSERT INTO tags(name, source) VALUES('嗆人', 'auto');
                    INSERT INTO entry_tags(entry_key, tag_name, source)
                    VALUES('meme_a1', '嗆人', 'auto');
                    """
                )
                # 真正的舊資料庫沒有版本號（user_version=0）；上面先 init_db 過，這裡還原成舊庫狀態。
                conn.execute("PRAGMA user_version = 0")
                conn.commit()
                init_db(conn)
                migrated = conn.execute(
                    "SELECT tag_id, source_type FROM entry_tags WHERE entry_key='meme_a1'"
                ).fetchone()
                self.assertEqual(tuple(migrated), ("reaction_roast", "rule"))
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(count(conn, "entry_tags"), 1)
            finally:
                conn.close()

    def test_agent_tagging_roundtrip_requires_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "memes.sqlite3"
            image_path = root / "meme.png"
            jobs_path = root / "jobs.jsonl"
            results_path = root / "results.jsonl"
            from PIL import Image
            Image.new("RGB", (8, 8)).save(image_path)

            conn = connect(db_path)
            try:
                init_db(conn)
                conn.execute(
                    "INSERT INTO discord_messages(id, timestamp, content) VALUES('m1', '2026-07-13T00:00:00+08:00', '')"
                )
                conn.execute(
                    """
                    INSERT INTO attachments(id, message_id, file_name, local_path, download_status)
                    VALUES('a1', 'm1', 'meme.png', ?, 'downloaded')
                    """,
                    (str(image_path),),
                )
                conn.execute(
                    """
                    INSERT INTO meme_entries(
                        key, attachment_id, source_message_id, quote_text,
                        pairing_method, confidence, status
                    ) VALUES('meme_a1', 'a1', 'm1', '你說什麼', 'test', 1.0, 'ready')
                    """
                )
                conn.commit()

                exported = export_tagging_jobs(conn, jobs_path, actor_name="codex", limit=10)
                self.assertEqual(exported["jobs"], 1)
                job = json.loads(jobs_path.read_text(encoding="utf-8").strip())
                self.assertEqual(job["key"], "meme_a1")
                self.assertTrue(Path(job["image_path"]).is_absolute())

                results_path.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "run_id": exported["run_id"],
                            "key": "meme_a1",
                            "image_summary": "角色正以憤怒表情回嘴",
                            "suggestions": [
                                {
                                    "tag_id": "reaction_roast",
                                    "confidence": 0.86,
                                    "reason": "台詞和表情都呈現直接回擊",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                imported = import_tagging_results(conn, results_path)
                self.assertEqual(imported["created_suggestions"], 1)
                self.assertEqual(count(conn, "entry_tags"), 0)
                repeated = import_tagging_results(conn, results_path)
                self.assertEqual(repeated["duplicates"], 1)
            finally:
                conn.close()

            app = create_app(db_path=db_path)
            client = app.test_client()
            item = client.get("/api/entries/meme_a1").get_json()
            self.assertEqual(item["tag_suggestions"][0]["label"], "嗆人")
            suggestion_id = item["tag_suggestions"][0]["id"]
            accepted = client.post(
                f"/api/tag-suggestions/{suggestion_id}/decision",
                json={"decision": "accepted", "reviewed_by": "tester"},
            )
            self.assertEqual(accepted.status_code, 200)
            self.assertIn("嗆人", client.get("/api/entries/meme_a1").get_json()["tags"])
            self.assertEqual(
                client.post(
                    f"/api/tag-suggestions/{suggestion_id}/decision",
                    json={"decision": "rejected", "reviewed_by": "tester"},
                ).status_code,
                409,
            )

    def test_agent_tagging_rejects_unknown_taxonomy_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "memes.sqlite3"
            results_path = root / "results.jsonl"
            conn = connect(db_path)
            try:
                init_db(conn)
                conn.execute(
                    "INSERT INTO discord_messages(id, timestamp, content) VALUES('m1', '2026-07-13', '')"
                )
                conn.execute(
                    "INSERT INTO attachments(id, message_id, file_name, local_path) VALUES('a1', 'm1', 'x.png', 'x.png')"
                )
                conn.execute(
                    """
                    INSERT INTO meme_entries(key, attachment_id, source_message_id, quote_text, pairing_method, confidence, status)
                    VALUES('meme_a1', 'a1', 'm1', '', 'test', 1, 'ready')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO tagging_runs(id, actor_type, actor_name, taxonomy_version, input_path, exported_count)
                    VALUES('run-1', 'agent', 'codex', 2, 'jobs.jsonl', 1)
                    """
                )
                conn.execute(
                    "INSERT INTO tagging_run_entries(run_id, entry_key, image_path) VALUES('run-1', 'meme_a1', 'x.png')"
                )
                conn.commit()
                results_path.write_text(
                    '{"schema_version":1,"run_id":"run-1","key":"meme_a1","image_summary":"test","suggestions":[{"tag_id":"made_up","confidence":0.9,"reason":"test"}]}\n',
                    encoding="utf-8",
                )
                summary = import_tagging_results(conn, results_path)
                self.assertEqual(len(summary["errors"]), 1)
                self.assertEqual(count(conn, "tag_suggestions"), 0)
            finally:
                conn.close()

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
                    "source_page": "https://example.org/posts/42?image=2",
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
            entry_url = "/api/entries/" + item["key"]
            self.assertEqual(client.get(entry_url).get_json()["source_page"], "https://example.org/posts/42?image=2")
            self.assertEqual(client.patch(entry_url, json={"source_page": None}).status_code, 200)
            self.assertIsNone(client.get(entry_url).get_json()["source_page"])
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
            self.assertIn("無法讀取圖片", response.get_json()["error"]["message"])
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

                def fake_download(_url, output_path, timeout=30, *, force=False):
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
