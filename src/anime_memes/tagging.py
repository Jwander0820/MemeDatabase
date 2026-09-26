from __future__ import annotations

import json
import math
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .tags import DEFAULT_TAXONOMY_PATH, load_taxonomy, sync_taxonomy, taxonomy_indexes


ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def export_tagging_jobs(
    conn: sqlite3.Connection,
    output_path: str | Path,
    *,
    actor_name: str,
    model_name: str | None = None,
    limit: int = 50,
    status: str = "ready",
    untagged: bool = True,
    taxonomy_path: str | Path = DEFAULT_TAXONOMY_PATH,
) -> dict[str, Any]:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if not actor_name.strip():
        raise ValueError("actor name is required")
    taxonomy = load_taxonomy(taxonomy_path)
    sync_taxonomy(conn, taxonomy)

    where = ["a.local_path IS NOT NULL", "a.local_path != ''"]
    params: list[Any] = []
    if status != "all":
        where.append("e.status = ?")
        params.append(status)
    if untagged:
        where.append("NOT EXISTS (SELECT 1 FROM entry_tags et WHERE et.entry_key=e.key)")
    where.append(
        """
        NOT EXISTS (
            SELECT 1 FROM tag_suggestions ts
            JOIN tagging_runs tr ON tr.id=ts.run_id
            WHERE ts.entry_key=e.key AND ts.status='pending' AND tr.taxonomy_version=?
        )
        """
    )
    params.append(int(taxonomy["version"]))
    rows = conn.execute(
        f"""
        SELECT e.key, e.anime_title, e.speaker, e.quote_text, e.ocr_text,
               a.local_path, a.file_name,
               COALESCE(GROUP_CONCAT(DISTINCT et.tag_name), '') AS existing_tags
        FROM meme_entries e
        JOIN attachments a ON a.id=e.attachment_id
        LEFT JOIN entry_tags et ON et.entry_key=e.key
        WHERE {' AND '.join(where)}
        GROUP BY e.key
        ORDER BY e.updated_at DESC, e.key
        """,
        params,
    ).fetchall()

    run_id = str(uuid.uuid4())
    output = Path(output_path)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    jobs: list[dict[str, Any]] = []
    skipped_missing = 0
    skipped_unsupported = 0
    skipped_invalid = 0
    for row in rows:
        path = resolve_image_path(str(row["local_path"]))
        suffix = (path.suffix or Path(str(row["file_name"] or "")).suffix).lower()
        if suffix not in SUPPORTED_IMAGE_SUFFIXES:
            skipped_unsupported += 1
            continue
        if not path.is_file() or path.stat().st_size <= 0:
            skipped_missing += 1
            continue
        try:
            with Image.open(path) as picture:
                picture.verify()
            with Image.open(path) as picture:
                picture.load()
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            skipped_invalid += 1
            continue
        jobs.append(
            {
                "schema_version": 1,
                "run_id": run_id,
                "taxonomy_version": int(taxonomy["version"]),
                "key": str(row["key"]),
                "image_path": path.resolve().as_posix(),
                "anime_title": row["anime_title"],
                "speaker": row["speaker"],
                "quote_text": row["quote_text"],
                "ocr_text": row["ocr_text"],
                "existing_tags": [tag for tag in str(row["existing_tags"] or "").split(",") if tag],
            }
        )
        if len(jobs) >= limit:
            break

    output.write_text(
        "".join(json.dumps(job, ensure_ascii=False) + "\n" for job in jobs),
        encoding="utf-8",
    )
    conn.execute(
        """
        INSERT INTO tagging_runs(
            id, actor_type, actor_name, model_name, taxonomy_version,
            input_path, status, exported_count
        ) VALUES(?, 'agent', ?, ?, ?, ?, 'exported', ?)
        """,
        (run_id, actor_name.strip(), model_name or None, int(taxonomy["version"]), str(output), len(jobs)),
    )
    conn.executemany(
        "INSERT INTO tagging_run_entries(run_id, entry_key, image_path) VALUES(?, ?, ?)",
        [(run_id, job["key"], job["image_path"]) for job in jobs],
    )
    conn.commit()
    return {
        "run_id": run_id,
        "taxonomy_version": int(taxonomy["version"]),
        "jobs": len(jobs),
        "skipped_missing": skipped_missing,
        "skipped_unsupported": skipped_unsupported,
        "skipped_invalid": skipped_invalid,
        "output": str(output),
    }


def import_tagging_results(
    conn: sqlite3.Connection,
    results_path: str | Path,
    *,
    taxonomy_path: str | Path = DEFAULT_TAXONOMY_PATH,
) -> dict[str, Any]:
    taxonomy = load_taxonomy(taxonomy_path)
    sync_taxonomy(conn, taxonomy)
    by_id, _by_label = taxonomy_indexes(taxonomy)
    category_limits = {str(item["id"]): int(item["max_per_entry"]) for item in taxonomy["categories"]}
    policy = taxonomy["policy"]
    path = Path(results_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    summary: dict[str, Any] = {
        "processed_entries": 0,
        "created_suggestions": 0,
        "empty_results": 0,
        "duplicates": 0,
        "below_threshold": 0,
        "errors": [],
    }
    touched_runs: set[str] = set()
    seen_entries: set[tuple[str, str]] = set()

    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            if not isinstance(item, dict) or item.get("schema_version") != 1:
                raise ValueError("schema_version must be 1")
            run_id = required_text(item, "run_id")
            key = required_text(item, "key")
            pair = (run_id, key)
            if pair in seen_entries:
                raise ValueError("duplicate result line for entry")
            seen_entries.add(pair)
            run = conn.execute("SELECT * FROM tagging_runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise ValueError(f"unknown run: {run_id}")
            if int(run["taxonomy_version"]) != int(taxonomy["version"]):
                raise RuntimeError("taxonomy version mismatch")
            member = conn.execute(
                "SELECT status FROM tagging_run_entries WHERE run_id=? AND entry_key=?",
                (run_id, key),
            ).fetchone()
            if member is None:
                raise ValueError(f"entry does not belong to run: {key}")
            image_summary = required_text(item, "image_summary")
            suggestions = item.get("suggestions")
            if not isinstance(suggestions, list):
                raise ValueError("suggestions must be a list")
            if len(suggestions) > int(policy["max_tags_per_entry"]):
                raise ValueError("too many suggestions")

            validated: list[tuple[str, float, str]] = []
            tag_ids: set[str] = set()
            category_counts: dict[str, int] = {}
            for suggestion in suggestions:
                if not isinstance(suggestion, dict):
                    raise ValueError("suggestion must be an object")
                tag_id = required_text(suggestion, "tag_id")
                tag = by_id.get(tag_id)
                if tag is None or not tag.get("enabled") or not tag.get("model_selectable"):
                    raise ValueError(f"unknown or unavailable tag: {tag_id}")
                if tag_id in tag_ids:
                    raise ValueError(f"duplicate tag in result: {tag_id}")
                tag_ids.add(tag_id)
                confidence = suggestion.get("confidence")
                if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                    raise ValueError(f"invalid confidence: {tag_id}")
                confidence = float(confidence)
                if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                    raise ValueError(f"invalid confidence: {tag_id}")
                reason = required_text(suggestion, "reason")
                category = str(tag["category"])
                category_counts[category] = category_counts.get(category, 0) + 1
                if category_counts[category] > category_limits[category]:
                    raise ValueError(f"category limit exceeded: {category}")
                validated.append((tag_id, confidence, reason))

            for tag_id, confidence, reason in validated:
                if confidence < float(policy["minimum_confidence"]):
                    summary["below_threshold"] += 1
                    continue
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO tag_suggestions(
                        run_id, entry_key, tag_id, confidence, reason, image_summary
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, key, tag_id, confidence, reason, image_summary),
                )
                if cursor.rowcount:
                    summary["created_suggestions"] += 1
                else:
                    summary["duplicates"] += 1
            if not suggestions:
                summary["empty_results"] += 1
            conn.execute(
                "UPDATE tagging_run_entries SET status='processed', processed_at=CURRENT_TIMESTAMP WHERE run_id=? AND entry_key=?",
                (run_id, key),
            )
            conn.execute(
                "UPDATE tagging_runs SET output_path=?, status='awaiting_review', imported_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(path.resolve()), run_id),
            )
            touched_runs.add(run_id)
            summary["processed_entries"] += 1
        except RuntimeError as exc:
            conn.rollback()
            raise ValueError(f"line {line_number}: {exc}") from exc
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            summary["errors"].append({"line": line_number, "error": str(exc)})

    for run_id in touched_runs:
        counts = conn.execute(
            """
            SELECT
              SUM(CASE WHEN status='processed' THEN 1 ELSE 0 END) AS processed,
              COUNT(*) AS total
            FROM tagging_run_entries WHERE run_id=?
            """,
            (run_id,),
        ).fetchone()
        suggestion_count = conn.execute(
            "SELECT COUNT(*) FROM tag_suggestions WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
        pending_count = conn.execute(
            "SELECT COUNT(*) FROM tag_suggestions WHERE run_id=? AND status='pending'",
            (run_id,),
        ).fetchone()[0]
        run_status = "completed" if int(counts["processed"] or 0) == int(counts["total"] or 0) and not pending_count else "awaiting_review"
        conn.execute(
            """
            UPDATE tagging_runs SET processed_count=?, suggestion_count=?, error_count=?,
                status=?, completed_at=CASE WHEN ?='completed' THEN CURRENT_TIMESTAMP ELSE completed_at END
            WHERE id=?
            """,
            (
                int(counts["processed"] or 0),
                int(suggestion_count),
                len(summary["errors"]),
                run_status,
                run_status,
                run_id,
            ),
        )
    conn.commit()
    return summary


def list_tag_suggestions(
    conn: sqlite3.Connection,
    entry_key: str,
    *,
    status: str = "pending",
) -> list[dict[str, Any]]:
    allowed = {"pending", "accepted", "rejected", "all"}
    if status not in allowed:
        raise ValueError("invalid suggestion status")
    where = "ts.entry_key=?"
    params: list[Any] = [entry_key]
    if status != "all":
        where += " AND ts.status=?"
        params.append(status)
    rows = conn.execute(
        f"""
        SELECT ts.*, t.name AS label, t.category, tr.actor_type, tr.actor_name,
               tr.model_name, tr.taxonomy_version
        FROM tag_suggestions ts
        JOIN tags t ON t.id=ts.tag_id
        JOIN tagging_runs tr ON tr.id=ts.run_id
        WHERE {where}
        ORDER BY ts.created_at DESC, ts.id DESC
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def decide_tag_suggestion(
    conn: sqlite3.Connection,
    suggestion_id: int,
    *,
    decision: str,
    reviewed_by: str,
) -> dict[str, Any]:
    if decision not in {"accepted", "rejected"}:
        raise ValueError("decision must be accepted or rejected")
    if not reviewed_by.strip():
        raise ValueError("reviewed_by is required")
    row = conn.execute(
        """
        SELECT ts.*, t.name AS label, t.enabled, t.model_selectable FROM tag_suggestions ts
        JOIN tags t ON t.id=ts.tag_id WHERE ts.id=?
        """,
        (suggestion_id,),
    ).fetchone()
    if row is None:
        raise LookupError("suggestion not found")
    if row["status"] != "pending":
        if row["status"] == decision:
            return dict(row)
        raise RuntimeError("suggestion already has a different decision")

    if decision == "accepted" and (not row["enabled"] or not row["model_selectable"]):
        raise ValueError("tag is no longer available for suggestions")

    cursor = conn.execute(
        """
        UPDATE tag_suggestions SET status=?, reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP
        WHERE id=? AND status='pending'
        """,
        (decision, reviewed_by.strip(), suggestion_id),
    )
    if not cursor.rowcount:
        current = conn.execute(
            "SELECT ts.*, t.name AS label FROM tag_suggestions ts JOIN tags t ON t.id=ts.tag_id WHERE ts.id=?",
            (suggestion_id,),
        ).fetchone()
        if current and current["status"] == decision:
            return dict(current)
        raise RuntimeError("suggestion already has a different decision")
    if decision == "accepted":
        conn.execute(
            """
            INSERT OR REPLACE INTO entry_tags(
                entry_key, tag_name, source, tag_id, source_type, source_ref,
                confidence, reason
            ) VALUES(?, ?, 'suggestion', ?, 'suggestion', ?, ?, ?)
            """,
            (
                row["entry_key"],
                row["label"],
                row["tag_id"],
                str(suggestion_id),
                row["confidence"],
                row["reason"],
            ),
        )
        conn.execute("UPDATE meme_entries SET updated_at=CURRENT_TIMESTAMP WHERE key=?", (row["entry_key"],))
    pending = conn.execute(
        "SELECT COUNT(*) FROM tag_suggestions WHERE run_id=? AND status='pending'",
        (row["run_id"],),
    ).fetchone()[0]
    remaining = conn.execute(
        "SELECT COUNT(*) FROM tagging_run_entries WHERE run_id=? AND status!='processed'",
        (row["run_id"],),
    ).fetchone()[0]
    if not pending and not remaining:
        conn.execute(
            "UPDATE tagging_runs SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE id=?",
            (row["run_id"],),
        )
    conn.commit()
    updated = conn.execute(
        """
        SELECT ts.*, t.name AS label FROM tag_suggestions ts
        JOIN tags t ON t.id=ts.tag_id WHERE ts.id=?
        """,
        (suggestion_id,),
    ).fetchone()
    return dict(updated)


def required_text(item: dict[str, Any], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def resolve_image_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path
