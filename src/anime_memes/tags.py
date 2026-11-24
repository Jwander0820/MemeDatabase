from __future__ import annotations

import json
import hashlib
import math
import sqlite3
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TAXONOMY_PATH = ROOT / "config" / "tags.taxonomy.json"


def load_taxonomy(path: str | Path = DEFAULT_TAXONOMY_PATH) -> dict[str, Any]:
    taxonomy_path = Path(path)
    if not taxonomy_path.exists():
        raise ValueError(f"taxonomy not found: {taxonomy_path}")
    try:
        data = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid taxonomy file: {exc}") from exc
    validate_taxonomy(data)
    return data


def tag_catalog(conn: sqlite3.Connection, taxonomy_path: str | Path = DEFAULT_TAXONOMY_PATH) -> dict[str, Any]:
    """管理頁標籤輸入用的清單：分類內的啟用標籤（含別名）加上仍在使用的分類外標籤與使用次數。"""
    taxonomy = load_taxonomy(taxonomy_path)
    counts = {row[0]: row[1] for row in conn.execute(
        "SELECT tag_name, COUNT(DISTINCT entry_key) FROM entry_tags GROUP BY tag_name")}
    tags, seen = [], set()
    for tag in taxonomy["tags"]:
        if not tag.get("enabled"):
            continue
        seen.add(tag["label"])
        tags.append({"name": tag["label"], "category": tag["category"], "description": tag.get("description", ""),
                     "aliases": list(tag.get("aliases", [])), "count": counts.get(tag["label"], 0), "in_taxonomy": True})
    for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        if name not in seen:
            tags.append({"name": name, "category": "legacy", "description": "", "aliases": [],
                         "count": count, "in_taxonomy": False})
    return {
        "categories": [{"id": c["id"], "label": c["label"]} for c in taxonomy["categories"]],
        "tags": tags,
        "max_tags_per_entry": int(taxonomy.get("policy", {}).get("max_tags_per_entry", 6)),
    }


def validate_taxonomy(data: Any) -> None:
    if not isinstance(data, dict) or data.get("version") != 2:
        raise ValueError("taxonomy version must be 2")
    policy = data.get("policy")
    if not isinstance(policy, dict):
        raise ValueError("taxonomy policy is required")
    max_tags = policy.get("max_tags_per_entry")
    minimum = policy.get("minimum_confidence")
    if type(max_tags) is not int or not 1 <= max_tags <= 20:
        raise ValueError("max_tags_per_entry must be between 1 and 20")
    if not isinstance(minimum, (int, float)) or isinstance(minimum, bool) or not 0 <= float(minimum) <= 1:
        raise ValueError("minimum_confidence must be between 0 and 1")
    if policy.get("allow_unknown_tags") is not False:
        raise ValueError("allow_unknown_tags must be false")

    categories = data.get("categories")
    tags = data.get("tags")
    if not isinstance(categories, list) or not isinstance(tags, list):
        raise ValueError("categories and tags must be lists")
    category_ids: set[str] = set()
    category_limits: dict[str, int] = {}
    for category in categories:
        if not isinstance(category, dict):
            raise ValueError("category must be an object")
        category_id = str(category.get("id") or "")
        if not valid_identifier(category_id) or category_id in category_ids:
            raise ValueError(f"invalid or duplicate category id: {category_id}")
        limit = category.get("max_per_entry")
        if type(limit) is not int or limit < 1:
            raise ValueError(f"invalid category limit: {category_id}")
        category_ids.add(category_id)
        category_limits[category_id] = limit

    tag_ids: set[str] = set()
    labels: set[str] = set()
    for tag in tags:
        if not isinstance(tag, dict):
            raise ValueError("tag must be an object")
        tag_id = str(tag.get("id") or "")
        label = str(tag.get("label") or "").strip()
        category = str(tag.get("category") or "")
        if not valid_identifier(tag_id) or tag_id in tag_ids:
            raise ValueError(f"invalid or duplicate tag id: {tag_id}")
        if not label or label in labels or "\ufffd" in label:
            raise ValueError(f"invalid or duplicate tag label: {label}")
        if category not in category_ids:
            raise ValueError(f"unknown category for {tag_id}: {category}")
        if not str(tag.get("description") or "").strip():
            raise ValueError(f"description is required: {tag_id}")
        if not isinstance(tag.get("enabled"), bool) or not isinstance(tag.get("model_selectable"), bool):
            raise ValueError(f"enabled/model_selectable must be booleans: {tag_id}")
        for field in ("aliases", "text_keywords", "visual_cues", "positive_examples", "negative_examples"):
            if not isinstance(tag.get(field), list) or any(not isinstance(value, str) or not value.strip() for value in tag[field]):
                raise ValueError(f"{field} must be a list of nonempty strings: {tag_id}")
        tag_ids.add(tag_id)
        labels.add(label)
    alias_owners = {tag["label"]: tag["id"] for tag in tags}
    for tag in tags:
        for alias in tag["aliases"]:
            owner = alias_owners.setdefault(alias, tag["id"])
            if owner != tag["id"]:
                raise ValueError(f"ambiguous tag alias: {alias}")


def valid_identifier(value: str) -> bool:
    return bool(value) and value.isascii() and value[0].islower() and all(char.islower() or char.isdigit() or char == "_" for char in value)


def taxonomy_indexes(taxonomy: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id = {str(tag["id"]): tag for tag in taxonomy["tags"]}
    by_label: dict[str, dict[str, Any]] = {}
    for tag in taxonomy["tags"]:
        by_label[str(tag["label"])] = tag
        for alias in tag.get("aliases", []):
            by_label.setdefault(str(alias), tag)
    return by_id, by_label


def sync_taxonomy(conn: sqlite3.Connection, taxonomy: dict[str, Any]) -> None:
    validate_taxonomy(taxonomy)
    if not conn.in_transaction:
        conn.execute("BEGIN")
    deferred = conn.execute("PRAGMA defer_foreign_keys").fetchone()[0]
    conn.execute("SAVEPOINT taxonomy_sync")
    conn.execute("PRAGMA defer_foreign_keys=ON")
    try:
        _sync_taxonomy_rows(conn, taxonomy)
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("taxonomy migration would leave broken tag references")
    except Exception:
        conn.execute("ROLLBACK TO taxonomy_sync")
        raise
    finally:
        conn.execute("RELEASE taxonomy_sync")
        conn.execute(f"PRAGMA defer_foreign_keys={int(deferred)}")


def _merge_tag(conn: sqlite3.Connection, old_name: str, new_name: str, tag_id: str) -> None:
    """Normalize an alias; keep an existing canonical link on duplicate source/key."""
    old = conn.execute("SELECT id FROM tags WHERE name=?", (old_name,)).fetchone()
    if old is None or old_name == new_name:
        return
    if old["id"] and not str(old["id"]).startswith("legacy_") and old["id"] != tag_id:
        raise ValueError(f"tag name belongs to a different stable ID: {old_name}")
    conn.execute(
        """
        INSERT INTO entry_tags(entry_key, tag_name, source, tag_id, source_type,
                               source_ref, confidence, reason, created_at)
        SELECT entry_key, ?, source, ?, source_type, source_ref, confidence, reason, created_at
        FROM entry_tags WHERE tag_name=?
        ON CONFLICT(entry_key, tag_name, source) DO NOTHING
        """,
        (new_name, tag_id, old_name),
    )
    conn.execute("DELETE FROM entry_tags WHERE tag_name=?", (old_name,))
    if old["id"]:
        conn.execute("UPDATE tag_suggestions SET tag_id=? WHERE tag_id=?", (tag_id, old["id"]))
    conn.execute("DELETE FROM tags WHERE name=?", (old_name,))


def _sync_taxonomy_rows(conn: sqlite3.Connection, taxonomy: dict[str, Any]) -> None:
    version = int(taxonomy["version"])
    # Stage all renames first so exchanging two labels preserves both stable IDs.
    for tag in taxonomy["tags"]:
        existing = conn.execute("SELECT name FROM tags WHERE id=?", (tag["id"],)).fetchone()
        if existing and existing["name"] != tag["label"]:
            temporary_name = f"__rename_{uuid.uuid4().hex}"
            conn.execute("UPDATE tags SET name=? WHERE id=?", (temporary_name, tag["id"]))
            conn.execute("UPDATE entry_tags SET tag_name=? WHERE tag_name=?", (temporary_name, existing["name"]))
    for tag in taxonomy["tags"]:
        label = str(tag["label"])
        tag_id = str(tag["id"])
        existing = conn.execute("SELECT name FROM tags WHERE id=?", (tag_id,)).fetchone()
        if existing and existing["name"] != label:
            old_name = str(existing["name"])
            _merge_tag(conn, label, old_name, tag_id)
            conn.execute("UPDATE tags SET name=? WHERE id=?", (label, tag_id))
            conn.execute("UPDATE entry_tags SET tag_name=? WHERE tag_name=?", (label, old_name))
        previous = conn.execute("SELECT id FROM tags WHERE name=?", (label,)).fetchone()
        if previous and previous["id"] and previous["id"] != tag_id:
            conn.execute("UPDATE tag_suggestions SET tag_id=? WHERE tag_id=?", (tag_id, previous["id"]))
        conn.execute(
            """
            INSERT INTO tags(
                name, source, id, category, description, enabled,
                model_selectable, taxonomy_version, updated_at
            ) VALUES(?, 'taxonomy', ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(name) DO UPDATE SET
                id=excluded.id,
                category=excluded.category,
                description=excluded.description,
                enabled=excluded.enabled,
                model_selectable=excluded.model_selectable,
                taxonomy_version=excluded.taxonomy_version,
                updated_at=CURRENT_TIMESTAMP
            WHERE tags.id IS NOT excluded.id
               OR tags.category IS NOT excluded.category
               OR tags.description IS NOT excluded.description
               OR tags.enabled IS NOT excluded.enabled
               OR tags.model_selectable IS NOT excluded.model_selectable
               OR tags.taxonomy_version IS NOT excluded.taxonomy_version
            """,
            (
                label,
                str(tag["id"]),
                str(tag["category"]),
                str(tag["description"]),
                int(bool(tag["enabled"])),
                int(bool(tag["model_selectable"])),
                version,
            ),
        )
        conn.execute(
            "UPDATE entry_tags SET tag_id=? WHERE tag_name=? AND tag_id IS NOT ?",
            (tag_id, label, tag_id),
        )
        for alias in tag.get("aliases", []):
            _merge_tag(conn, str(alias), label, tag_id)

    for row in conn.execute("SELECT name FROM tags WHERE id IS NULL OR id = ''").fetchall():
        label = str(row["name"])
        conn.execute(
            """
            UPDATE tags SET id=?, category='legacy', enabled=0, model_selectable=0,
                taxonomy_version=?, updated_at=CURRENT_TIMESTAMP
            WHERE name=?
            """,
            (legacy_tag_id(label), version, label),
        )
    # Repair rows already migrated by the earlier implementation. Explicitly
    # created custom tags carry a description and remain available to people.
    conn.execute(
        """UPDATE tags SET enabled=0, updated_at=CURRENT_TIMESTAMP
           WHERE category='legacy' AND description='' AND model_selectable=0
             AND id LIKE 'legacy_%' AND enabled!=0"""
    )
    conn.execute(
        """
        UPDATE entry_tags
        SET tag_id=(SELECT id FROM tags WHERE tags.name=entry_tags.tag_name)
        WHERE tag_id IS NULL OR tag_id = ''
        """
    )


def legacy_tag_id(label: str) -> str:
    digest = hashlib.sha1(label.encode("utf-8")).hexdigest()[:12]
    return f"legacy_{digest}"


def load_rules(path: str | Path) -> list[dict[str, Any]]:
    rules_path = Path(path)
    if not rules_path.exists():
        raise ValueError(f"tag rules not found: {rules_path}")
    data = json.loads(rules_path.read_text(encoding="utf-8"))
    if data.get("version") == 2:
        validate_taxonomy(data)
        return rules_from_taxonomy(data)
    if data.get("version", 1) != 1:
        raise ValueError("unsupported tag rules version")
    rules = data.get("rules", [])
    if isinstance(rules, list):
        return rules
    return []


def rules_from_taxonomy(taxonomy: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"tag": tag["label"], "keywords": tag.get("text_keywords", [])}
        for tag in taxonomy["tags"]
        if tag.get("enabled") and tag.get("text_keywords")
    ]


def match_tags(entry: dict[str, Any], rules: list[dict[str, Any]]) -> list[tuple[str, str]]:
    haystack = "\n".join(
        str(entry.get(field) or "")
        for field in ("anime_title", "speaker", "quote_text", "ocr_text")
    ).lower()
    matched: list[tuple[str, str]] = []
    for rule in rules:
        tag = str(rule.get("tag") or "").strip()
        if not tag:
            continue
        for keyword in rule.get("keywords", []):
            keyword_text = str(keyword).strip()
            if keyword_text and keyword_text.lower() in haystack:
                matched.append((tag, f"keyword:{keyword_text}"))
                break
    return matched


def add_entry_tag(
    conn: sqlite3.Connection,
    entry_key: str,
    tag_name: str,
    source: str,
    confidence: float | None = None,
    reason: str | None = None,
) -> None:
    tag_name = tag_name.strip()
    row = conn.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()
    if row is None:
        tag_id = legacy_tag_id(tag_name)
        conn.execute(
            """
            INSERT INTO tags(
                name, source, id, category, description, enabled,
                model_selectable, taxonomy_version, updated_at
            ) VALUES(?, ?, ?, 'legacy', ?, 1, 0, 2, CURRENT_TIMESTAMP)
            """,
            (tag_name, source, tag_id, "人工建立的相容標籤"),
        )
    else:
        tag_id = str(row["id"] or legacy_tag_id(tag_name))
        if not row["id"]:
            conn.execute("UPDATE tags SET id=?, updated_at=CURRENT_TIMESTAMP WHERE name=?", (tag_id, tag_name))
    source_type = {"auto": "rule", "manual": "manual", "local_llm": "suggestion"}.get(source, source)
    conn.execute(
        """
        INSERT OR REPLACE INTO entry_tags(
            entry_key, tag_name, source, tag_id, source_type, source_ref, confidence, reason
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (entry_key, tag_name, source, tag_id, source_type, None, confidence, reason),
    )


def import_manual_tags(conn: sqlite3.Connection, csv_path: str | Path) -> int:
    import csv

    count = 0
    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("key") or "").strip()
            if not key:
                continue
            for tag in split_tags(row.get("tags") or ""):
                add_entry_tag(conn, key, tag, "manual", None, row.get("notes"))
                count += 1
    conn.commit()
    return count


def import_llm_suggestions(conn: sqlite3.Connection, jsonl_path: str | Path) -> tuple[int, int]:
    """Import the legacy label-based format as pending suggestions."""
    taxonomy = load_taxonomy()
    sync_taxonomy(conn, taxonomy)
    _by_id, by_label = taxonomy_indexes(taxonomy)
    run_id = str(uuid.uuid4())
    source_path = str(Path(jsonl_path).resolve())
    conn.execute(
        """
        INSERT INTO tagging_runs(
            id, actor_type, actor_name, taxonomy_version, input_path, output_path, status
        ) VALUES(?, 'local_llm', 'legacy-import', ?, ?, ?, 'importing')
        """,
        (run_id, int(taxonomy["version"]), source_path, source_path),
    )
    imported = 0
    errors = 0
    for line_number, line in enumerate(Path(jsonl_path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            key = str(item["key"]).strip()
            confidence = float(item.get("confidence", 0.0))
            reason = str(item.get("reason") or "")
            suggested_tags = item.get("suggested_tags")
            if not key or not isinstance(suggested_tags, list):
                raise ValueError("missing key or suggested_tags")
            if isinstance(item.get("confidence"), bool) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("confidence must be a finite number between 0 and 1")
            if not reason.strip():
                raise ValueError("reason is required")
            if len(suggested_tags) > int(taxonomy["policy"]["max_tags_per_entry"]):
                raise ValueError("too many suggestions")
            validated = []
            category_counts: dict[str, int] = {}
            limits = {category["id"]: category["max_per_entry"] for category in taxonomy["categories"]}
            for label in suggested_tags:
                tag = by_label.get(str(label).strip())
                if tag is None or not tag.get("enabled") or not tag.get("model_selectable"):
                    raise ValueError(f"unknown or unavailable taxonomy tag: {label}")
                if tag["id"] in {value["id"] for value in validated}:
                    raise ValueError("duplicate tag")
                category = str(tag["category"])
                category_counts[category] = category_counts.get(category, 0) + 1
                if category_counts[category] > limits[category]:
                    raise ValueError(f"category limit exceeded: {category}")
                validated.append(tag)
            if conn.execute("SELECT 1 FROM meme_entries WHERE key=?", (key,)).fetchone() is None:
                raise ValueError(f"unknown entry: {key}")
            conn.execute(
                "INSERT OR IGNORE INTO tagging_run_entries(run_id, entry_key, image_path) VALUES(?, ?, '')",
                (run_id, key),
            )
            for tag in validated if confidence >= float(taxonomy["policy"]["minimum_confidence"]) else []:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO tag_suggestions(
                        run_id, entry_key, tag_id, confidence, reason, image_summary
                    ) VALUES(?, ?, ?, ?, ?, '')
                    """,
                    (run_id, key, str(tag["id"]), confidence, reason),
                )
                imported += int(cursor.rowcount > 0)
            conn.execute(
                "UPDATE tagging_run_entries SET status='processed', processed_at=CURRENT_TIMESTAMP WHERE run_id=? AND entry_key=?",
                (run_id, key),
            )
        except Exception as exc:
            errors += 1
            conn.execute(
                "INSERT INTO review_items(reason, details) VALUES(?, ?)",
                ("llm_import_error", f"line {line_number}: {exc}"),
            )
    conn.commit()
    conn.execute(
        """
        UPDATE tagging_runs SET status='awaiting_review', processed_count=(
            SELECT COUNT(*) FROM tagging_run_entries WHERE run_id=? AND status='processed'
        ), suggestion_count=?, error_count=?, imported_at=CURRENT_TIMESTAMP WHERE id=?
        """,
        (run_id, imported, errors, run_id),
    )
    conn.commit()
    return imported, errors


def split_tags(value: str) -> list[str]:
    normalized = value
    for separator in ("\uff0c", "\u3001", "\uff1b", ";", "\n"):
        normalized = normalized.replace(separator, ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]
