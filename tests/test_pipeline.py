from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.anime_memes.db import connect
from src.anime_memes.ingest import ingest_fixed_export, ocr_review_entries
from src.anime_memes.notion import export_notion_csv
from src.anime_memes.ocr import extract_paddle_text, run_ocr
from src.anime_memes.review import H, export_review_csv, import_review_corrections
from src.anime_memes.tags import import_llm_suggestions, import_manual_tags


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
                self.assertEqual(second["messages"], 5)
                self.assertEqual(count(conn, "discord_messages"), 5)
                self.assertEqual(count(conn, "attachments"), 3)
                self.assertEqual(count(conn, "meme_entries"), 3)
                self.assertEqual(count(conn, "review_items"), 0)
                self.assertGreaterEqual(count(conn, "entry_tags"), 2)

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


def count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
