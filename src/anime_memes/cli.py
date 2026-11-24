from __future__ import annotations

import argparse
import json
import sys
from contextlib import closing
from pathlib import Path

from .convert import convert_file
from .db import backup_database, connect, init_db
from .env import getenv, load_dotenv
from .exporter import default_before, export_and_convert, incremental_after, save_sync_state
from .ingest import ingest_fixed_export, ocr_review_entries
from .images import build_thumbnails, download_all_attachments
from .notion import export_notion_csv
from .review import export_review_csv, import_review_corrections
from .tagging import export_tagging_jobs, import_tagging_results
from .tags import import_llm_suggestions, import_manual_tags
from .web import run_web_app


DEFAULT_DB = "data/anime_memes.sqlite3"


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        message = str(exc)
        for secret in (getattr(args, "token", None), getenv("DISCORD_BOT_TOKEN")):
            if secret:
                message = message.replace(secret, "[REDACTED]")
        print(f"Error: {message}", file=sys.stderr)
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
    ingest.add_argument("--tag-rules", default=getenv("ANIME_MEMES_TAG_RULES", "config/tags.taxonomy.json"))
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

    sync = sub.add_parser("sync-discord", help="Back up, incrementally export, ingest, and report special cases")
    sync.add_argument("--after")
    sync.add_argument("--before", default=default_before())
    sync.add_argument("--raw-dir", default="data/raw_exports")
    sync.add_argument("--fixed-dir", default="data/fixed_exports")
    sync.add_argument("--backup-dir", default="data/backups")
    sync.add_argument("--channel-id", default=getenv("DISCORD_CHANNEL_ID"))
    sync.add_argument("--token", default=getenv("DISCORD_BOT_TOKEN"))
    sync.add_argument("--exe", default=getenv("DISCORD_CHAT_EXPORTER_EXE"))
    sync.add_argument("--skip-attachments", action="store_true")
    sync.add_argument("--skip-thumbnails", action="store_true")
    sync.set_defaults(func=cmd_sync_discord)

    sync_status = sub.add_parser("sync-status", help="Show the durable Discord incremental cursor")
    sync_status.add_argument("--channel-id", default=getenv("DISCORD_CHANNEL_ID"))
    sync_status.set_defaults(func=cmd_sync_status)

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

    llm = sub.add_parser("import-llm-tags", help="Deprecated: import legacy suggestions; prefer import-tagging-results")
    llm.add_argument("jsonl_path")
    llm.set_defaults(func=cmd_import_llm_tags)

    tagging_export = sub.add_parser("export-tagging-jobs", help="Export image-tagging jobs for an Agent")
    tagging_export.add_argument("--actor", required=True, help="Agent name, for example codex or antigravity")
    tagging_export.add_argument("--model")
    tagging_export.add_argument("--status", default="ready", choices=["ready", "needs_review", "ignored", "all"])
    tagging_export.add_argument("--limit", type=int, default=50)
    tagging_export.add_argument("--output", required=True)
    tagging_export.add_argument("--taxonomy", default="config/tags.taxonomy.json")
    tagging_scope = tagging_export.add_mutually_exclusive_group()
    tagging_scope.add_argument("--untagged", dest="untagged", action="store_true", default=True)
    tagging_scope.add_argument("--include-tagged", dest="untagged", action="store_false")
    tagging_export.set_defaults(func=cmd_export_tagging_jobs)

    tagging_import = sub.add_parser("import-tagging-results", help="Validate and import Agent tag suggestions")
    tagging_import.add_argument("results_path")
    tagging_import.add_argument("--taxonomy", default="config/tags.taxonomy.json")
    tagging_import.set_defaults(func=cmd_import_tagging_results)

    download = sub.add_parser("download-attachments", help="Download original Discord attachments")
    download.add_argument("--attachment-dir", default=getenv("ANIME_MEMES_ATTACHMENT_DIR", "data/attachments"))
    download.add_argument("--all", action="store_true", help="Download every attachment in the database")
    download.add_argument("--force", action="store_true", help="Redownload files even if they already exist")
    download.add_argument("--retry-failed", action="store_true", help="Retry rows previously marked as failed")
    download.add_argument("--limit", type=int)
    download.set_defaults(func=cmd_download_attachments)

    thumbs = sub.add_parser("build-thumbnails", help="Build local thumbnails for downloaded images")
    thumbs.add_argument("--thumbnail-dir", default=getenv("ANIME_MEMES_THUMBNAIL_DIR", "data/thumbnails"))
    thumbs.add_argument("--size", type=int, default=420)
    thumbs.add_argument("--missing", action="store_true", help="Only build missing thumbnails")
    thumbs.add_argument("--force", action="store_true")
    thumbs.add_argument("--limit", type=int)
    thumbs.set_defaults(func=cmd_build_thumbnails)

    web = sub.add_parser("web", help="Run the local browser/editor UI")
    web.add_argument("--host", default=getenv("ANIME_MEMES_WEB_HOST", "127.0.0.1"))
    web.add_argument("--port", type=int, default=int(getenv("ANIME_MEMES_WEB_PORT", "8765")))
    web.add_argument("--attachment-dir", default=getenv("ANIME_MEMES_ATTACHMENT_DIR", "data/attachments"))
    web.add_argument("--thumbnail-dir", default=getenv("ANIME_MEMES_THUMBNAIL_DIR", "data/thumbnails"))
    web.set_defaults(func=cmd_web)

    publication = sub.add_parser("publication", help="Manage local publication selection and export")
    actions = publication.add_subparsers(dest="publication_action", required=True)
    choose = actions.add_parser("select")
    choose.add_argument("key")
    choose.add_argument("--selected", choices=["yes", "no"])
    choose.add_argument("--original-url")
    actions.add_parser("check")
    export_public = actions.add_parser("export")
    export_public.add_argument("--root", default=getenv("ANIME_MEMES_RELEASE_ROOT", "data/releases"))
    publication.set_defaults(func=cmd_publication)
    return parser


def cmd_publication(args):
    from .publishing import update_selection, check_database, export_release
    if args.publication_action == "select":
        data = {}
        if args.selected is not None:
            data["publish_selected"] = args.selected == "yes"
        if args.original_url is not None:
            data["public_original_url"] = args.original_url
        with closing(connect(args.db)) as conn:
            init_db(conn)
            result = update_selection(conn, args.key, data)
    elif args.publication_action == "check":
        result = check_database(args.db)
    else:
        result = export_release(args.db, args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("valid") is False or result.get("exported") is False:
        raise SystemExit(2)


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
            init_db(conn)
            after = incremental_after(conn, args.channel_id)

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


def cmd_sync_discord(args: argparse.Namespace) -> None:
    if not args.token:
        raise ValueError("DISCORD_BOT_TOKEN is required")
    if not args.channel_id:
        raise ValueError("DISCORD_CHANNEL_ID is required")
    if not args.exe:
        raise ValueError("DISCORD_CHAT_EXPORTER_EXE is required")

    backup_path = backup_database(args.db, args.backup_dir)
    with closing(connect(args.db)) as conn:
        init_db(conn)
        before_count = int(conn.execute("SELECT COUNT(*) FROM discord_messages").fetchone()[0])
        after = args.after or incremental_after(conn, args.channel_id)

    raw_path, fixed_path = export_and_convert(
        exporter_exe=args.exe,
        token=args.token,
        channel_id=args.channel_id,
        raw_dir=args.raw_dir,
        fixed_dir=args.fixed_dir,
        after=after,
        before=args.before,
    )
    exported = json.loads(Path(fixed_path).read_text(encoding="utf-8"))
    messages = [item for item in exported.get("messages", []) if isinstance(item, dict)]
    exported_ids = {str(item.get("id")) for item in messages}

    with closing(connect(args.db)) as conn:
        prior = {
            str(row["id"]): (row["timestamp"], row["timestamp_edited"], row["content"], row["is_pinned"])
            for row in conn.execute("SELECT id, timestamp, timestamp_edited, content, is_pinned FROM discord_messages")
        }
        new_ids = [str(item.get("id")) for item in messages if str(item.get("id")) not in prior]
        changed_ids = []
        for item in messages:
            message_id = str(item.get("id"))
            signature = (item.get("timestamp"), item.get("timestampEdited"), item.get("content") or "", 1 if item.get("isPinned") else 0)
            if message_id in prior and prior[message_id] != signature:
                changed_ids.append(message_id)
        summary = ingest_fixed_export(
            conn,
            fixed_path,
            channel_id=args.channel_id,
            raw_path=str(raw_path),
            attachment_dir=getenv("ANIME_MEMES_ATTACHMENT_DIR", "data/attachments"),
            tag_rules_path=getenv("ANIME_MEMES_TAG_RULES", "config/tags.taxonomy.json"),
            discord_bot_token=args.token,
        )
        conn.execute(
            "UPDATE export_runs SET status='partial', error=?, after_value=?, before_value=? WHERE id=?",
            ("Media processing has not completed", after, args.before, int(summary["export_run_id"])),
        )
        conn.commit()

        deleted_candidates = []
        if after and args.before:
            for row in conn.execute("SELECT id, timestamp FROM discord_messages WHERE COALESCE(type, '') != 'Manual'"):
                timestamp = str(row["timestamp"])
                if after <= timestamp[:10] < args.before and str(row["id"]) not in exported_ids:
                    deleted_candidates.append(str(row["id"]))
        new_attachment_ids = [
            str(attachment.get("id"))
            for item in messages if str(item.get("id")) in new_ids
            for attachment in (item.get("attachments") or []) if isinstance(attachment, dict)
        ]
        special = {
            "edited_message_ids": changed_ids,
            "possibly_deleted_message_ids": deleted_candidates,
            "unpaired_entry_keys": [
                str(row["key"]) for row in conn.execute(
                    "SELECT key FROM meme_entries WHERE attachment_id IN ({}) AND pairing_method = 'unpaired'".format(
                        ",".join("?" for _ in new_attachment_ids)
                    ),
                    new_attachment_ids,
                )
            ] if new_attachment_ids else [],
            "multi_attachment_message_ids": [
                str(item.get("id")) for item in messages
                if str(item.get("id")) in new_ids and len(item.get("attachments") or []) > 1
            ],
        }

        attachment_summary = None
        thumbnail_summary = None
        if not args.skip_attachments:
            attachment_summary = download_all_attachments(
                conn,
                attachment_dir=getenv("ANIME_MEMES_ATTACHMENT_DIR", "data/attachments"),
                channel_id=args.channel_id,
                discord_bot_token=args.token,
                retry_failed=True,
            )
        if not args.skip_thumbnails:
            thumbnail_summary = build_thumbnails(
                conn,
                thumbnail_dir=getenv("ANIME_MEMES_THUMBNAIL_DIR", "data/thumbnails"),
                missing=True,
            )

        ordered_messages = sorted(messages, key=lambda item: str(item.get("timestamp") or ""))
        if ordered_messages:
            last_message_timestamp = ordered_messages[-1].get("timestamp")
            last_message_id = str(ordered_messages[-1].get("id"))
        else:
            latest = conn.execute(
                "SELECT id, timestamp FROM discord_messages WHERE COALESCE(type, '') != 'Manual' ORDER BY timestamp DESC, id DESC LIMIT 1"
            ).fetchone()
            last_message_timestamp = latest["timestamp"] if latest else None
            last_message_id = str(latest["id"]) if latest else None
        incomplete = bool(
            (attachment_summary or {}).get("failed", 0)
            or (thumbnail_summary or {}).get("failed", 0)
        )
        if incomplete:
            conn.execute(
                "UPDATE export_runs SET status='partial', error=?, after_value=?, before_value=? WHERE id=?",
                ("Media processing failed; retry sync to finish", after, args.before, int(summary["export_run_id"])),
            )
            conn.commit()
        else:
            conn.execute(
                "UPDATE export_runs SET status='success', error=NULL WHERE id=?",
                (int(summary["export_run_id"]),),
            )
            save_sync_state(
                conn,
                channel_id=args.channel_id,
                export_run_id=int(summary["export_run_id"]),
                after_value=after,
                covered_through=args.before,
                last_message_timestamp=last_message_timestamp,
                last_message_id=last_message_id,
            )
        row = conn.execute(
            "SELECT * FROM discord_sync_state WHERE channel_id = ?", (args.channel_id,),
        ).fetchone()
        sync_state = dict(row) if row else None
        next_after = incremental_after(conn, args.channel_id)

    state = "partial" if incomplete else "initial_import" if before_count == 0 else "updated" if new_ids or changed_ids else "no_changes"
    print(json.dumps({
        "state": state,
        "range": {"after": after, "before": args.before},
        "backup": str(backup_path) if backup_path else None,
        "raw_path": str(raw_path),
        "fixed_path": str(fixed_path),
        "cursor": {
            "covered_through": sync_state["covered_through"],
            "last_message_timestamp": sync_state["last_message_timestamp"],
            "last_message_id": sync_state["last_message_id"],
            "next_after": next_after,
            "overlap_days": sync_state["overlap_days"],
            "last_success_at": sync_state["last_success_at"],
        } if sync_state else None,
        "new_message_ids": new_ids,
        "ingest": summary,
        "attachments": attachment_summary,
        "thumbnails": thumbnail_summary,
        "special_cases": special,
    }, ensure_ascii=False, indent=2))
    if incomplete:
        raise SystemExit(2)


def cmd_sync_status(args: argparse.Namespace) -> None:
    if not args.channel_id:
        raise ValueError("DISCORD_CHANNEL_ID is required")
    with closing(connect(args.db)) as conn:
        init_db(conn)
        row = conn.execute(
            "SELECT * FROM discord_sync_state WHERE channel_id = ?",
            (args.channel_id,),
        ).fetchone()
        if row is None:
            print(json.dumps({
                "channel_id": args.channel_id,
                "initialized": False,
                "next_after": incremental_after(conn, args.channel_id),
            }, ensure_ascii=False, indent=2))
            return
        state = dict(row)
        print(json.dumps({
            "channel_id": args.channel_id,
            "initialized": True,
            "covered_through": state["covered_through"],
            "last_message_timestamp": state["last_message_timestamp"],
            "last_message_id": state["last_message_id"],
            "next_after": incremental_after(conn, args.channel_id),
            "overlap_days": state["overlap_days"],
            "last_success_at": state["last_success_at"],
            "last_export_run_id": state["last_export_run_id"],
        }, ensure_ascii=False, indent=2))


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
        init_db(conn)
        imported, errors = import_llm_suggestions(conn, args.jsonl_path)
    print(f"Imported pending local LLM suggestions: {imported}; errors: {errors}")
    if errors:
        raise SystemExit(2)


def cmd_export_tagging_jobs(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        init_db(conn)
        summary = export_tagging_jobs(
            conn,
            args.output,
            actor_name=args.actor,
            model_name=args.model,
            limit=args.limit,
            status=args.status,
            untagged=args.untagged,
            taxonomy_path=args.taxonomy,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_import_tagging_results(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        init_db(conn)
        summary = import_tagging_results(conn, args.results_path, taxonomy_path=args.taxonomy)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["errors"]:
        raise SystemExit(2)


def cmd_download_attachments(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        summary = download_all_attachments(
            conn,
            attachment_dir=args.attachment_dir,
            channel_id=getenv("DISCORD_CHANNEL_ID"),
            discord_bot_token=getenv("DISCORD_BOT_TOKEN"),
            force=args.force,
            retry_failed=args.retry_failed,
            limit=args.limit,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_build_thumbnails(args: argparse.Namespace) -> None:
    with closing(connect(args.db)) as conn:
        summary = build_thumbnails(
            conn,
            thumbnail_dir=args.thumbnail_dir,
            size=args.size,
            missing=args.missing,
            force=args.force,
            limit=args.limit,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_web(args: argparse.Namespace) -> None:
    run_web_app(
        db_path=args.db,
        host=args.host,
        port=args.port,
        attachment_dir=args.attachment_dir,
        thumbnail_dir=args.thumbnail_dir,
    )
