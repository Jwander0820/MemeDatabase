from __future__ import annotations

import argparse
import json
import sys
from contextlib import closing
from pathlib import Path

from .convert import convert_file
from .db import connect, init_db
from .env import getenv, load_dotenv
from .exporter import default_before, export_and_convert, overlap_after
from .ingest import ingest_fixed_export, ocr_review_entries
from .notion import export_notion_csv
from .review import export_review_csv, import_review_corrections
from .tags import import_llm_suggestions, import_manual_tags


DEFAULT_DB = "data/anime_memes.sqlite3"


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anime-memes", description="Local anime meme database pipeline")
    parser.add_argument("--db", default=getenv("ANIME_MEMES_DB", DEFAULT_DB))
    sub = parser.add_subparsers(required=True)

    init = sub.add_parser("init-db", help="Create or migrate the SQLite database")
    init.set_defaults(func=cmd_init_db)

    convert = sub.add_parser("convert", help="Prune raw DiscordChatExporter JSON")
    convert.add_argument("input_json")
    convert.add_argument("output_json", nargs="?")
    convert.set_defaults(func=cmd_convert)

    ingest = sub.add_parser("ingest", help="Import a fixed Discord export into SQLite")
    ingest.add_argument("fixed_json")
    ingest.add_argument("--channel-id", default=getenv("DISCORD_CHANNEL_ID", ""))
    ingest.add_argument("--raw-path")
    ingest.add_argument("--attachment-dir", default=getenv("ANIME_MEMES_ATTACHMENT_DIR", "data/attachments"))
    ingest.add_argument("--tag-rules", default=getenv("ANIME_MEMES_TAG_RULES", "config/tags.rules.json"))
    ingest.add_argument("--download-attachments", action="store_true")
    ingest.add_argument("--ocr", action="store_true")
    ingest.add_argument("--ocr-engine", default="paddle", choices=["paddle", "easyocr", "none"])
    ingest.add_argument("--ocr-device", default="auto", choices=["auto", "cpu", "gpu"])
    ingest.add_argument("--ocr-scope", default="all", choices=["all", "review"])
    ingest.add_argument("--ocr-confidence-threshold", type=float, default=0.7)
    ingest.set_defaults(func=cmd_ingest)

    ocr_review = sub.add_parser("ocr-review", help="Run OCR for low-confidence or review entries")
    ocr_review.add_argument("--attachment-dir", default=getenv("ANIME_MEMES_ATTACHMENT_DIR", "data/attachments"))
    ocr_review.add_argument("--engine", default="paddle", choices=["paddle", "easyocr", "none"])
    ocr_review.add_argument("--device", default="auto", choices=["auto", "cpu", "gpu"])
    ocr_review.add_argument("--confidence-threshold", type=float, default=0.7)
    ocr_review.add_argument("--limit", type=int)
    ocr_review.add_argument("--download-missing", action="store_true")
    ocr_review.add_argument("--retry-failed-downloads", action="store_true")
    ocr_review.add_argument("--order", choices=["confidence", "newest", "oldest"], default="confidence")
    ocr_review.set_defaults(func=cmd_ocr_review)

    export = sub.add_parser("export-discord", help="Run DiscordChatExporter and convert to fixed JSON")
    export.add_argument("--after")
    export.add_argument("--before", default=default_before())
    export.add_argument("--incremental", action="store_true")
    export.add_argument("--raw-dir", default="data/raw_exports")
    export.add_argument("--fixed-dir", default="data/fixed_exports")
    export.add_argument("--channel-id", default=getenv("DISCORD_CHANNEL_ID"))
    export.add_argument("--token", default=getenv("DISCORD_BOT_TOKEN"))
    export.add_argument("--exe", default=getenv("DISCORD_CHAT_EXPORTER_EXE"))
    export.set_defaults(func=cmd_export_discord)

    notion = sub.add_parser("export-notion", help="Export Notion-ready CSV")
    notion.add_argument("--output")
    notion.set_defaults(func=cmd_export_notion)

    review = sub.add_parser("export-review", help="Export needs-review entries to an editable CSV")
    review.add_argument("--output")
    review.set_defaults(func=cmd_export_review)

    corrections = sub.add_parser("import-review-corrections", help="Import edited review CSV corrections")
    corrections.add_argument("csv_path")
    corrections.add_argument("--mark-ready", action="store_true")
    corrections.set_defaults(func=cmd_import_review_corrections)

    manual = sub.add_parser("import-manual-tags", help="Import manual_tags.csv")
    manual.add_argument("csv_path")
    manual.set_defaults(func=cmd_import_manual_tags)

    llm = sub.add_parser("import-llm-tags", help="Import local LLM tag JSONL suggestions")
    llm.add_argument("jsonl_path")
    llm.set_defaults(func=cmd_import_llm_tags)

    return parser


def cmd_init_db(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        init_db(conn)
    print(f"Initialized database: {args.db}")


def cmd_convert(args: argparse.Namespace) -> None:
    output = convert_file(args.input_json, args.output_json)
    print(f"Converted: {output}")


def cmd_ingest(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        summary = ingest_fixed_export(
            conn,
            args.fixed_json,
            channel_id=args.channel_id,
            raw_path=args.raw_path,
            attachment_dir=args.attachment_dir,
            tag_rules_path=args.tag_rules,
            download_attachments=args.download_attachments,
            run_ocr=args.ocr,
            ocr_engine=args.ocr_engine,
            ocr_device=args.ocr_device,
            ocr_scope=args.ocr_scope,
            ocr_confidence_threshold=args.ocr_confidence_threshold,
            discord_bot_token=getenv("DISCORD_BOT_TOKEN"),
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_ocr_review(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        summary = ocr_review_entries(
            conn,
            attachment_dir=args.attachment_dir,
            engine=args.engine,
            device=args.device,
            confidence_threshold=args.confidence_threshold,
            limit=args.limit,
            download_missing=args.download_missing,
            retry_failed_downloads=args.retry_failed_downloads,
            order=args.order,
            channel_id=getenv("DISCORD_CHANNEL_ID"),
            discord_bot_token=getenv("DISCORD_BOT_TOKEN"),
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_export_discord(args: argparse.Namespace) -> None:
    if not args.token:
        raise ValueError("DISCORD_BOT_TOKEN is required")
    if not args.channel_id:
        raise ValueError("DISCORD_CHANNEL_ID is required")
    if not args.exe:
        raise ValueError("DISCORD_CHAT_EXPORTER_EXE is required")
    after = args.after
    if args.incremental and not after:
        with closing(connect(args.db)) as conn:
            row = conn.execute(
                """
                SELECT before_value
                FROM export_runs
                WHERE status = 'success' AND channel_id = ? AND before_value IS NOT NULL
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (args.channel_id,),
            ).fetchone()
        after = overlap_after(row["before_value"]) if row else None

    raw_path, fixed_path = export_and_convert(
        exporter_exe=args.exe,
        token=args.token,
        channel_id=args.channel_id,
        raw_dir=args.raw_dir,
        fixed_dir=args.fixed_dir,
        after=after,
        before=args.before,
    )
    print(f"Raw export: {raw_path}")
    print(f"Fixed export: {fixed_path}")


def cmd_export_notion(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        output = export_notion_csv(conn, args.output)
    print(f"Notion CSV: {output}")


def cmd_export_review(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        output = export_review_csv(conn, args.output)
    print(f"Review CSV: {output}")


def cmd_import_review_corrections(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        summary = import_review_corrections(conn, args.csv_path, mark_ready=args.mark_ready)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_import_manual_tags(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        count = import_manual_tags(conn, args.csv_path)
    print(f"Imported manual tags: {count}")


def cmd_import_llm_tags(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        imported, errors = import_llm_suggestions(conn, args.jsonl_path)
    print(f"Imported local LLM tags: {imported}; errors: {errors}")
