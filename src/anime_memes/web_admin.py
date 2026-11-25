from __future__ import annotations

from flask import Blueprint, abort, current_app, g, jsonify, request

from . import management
from .media import configured_media_root, media_path
from .public_data import int_param
from .queries import fetch_entry, metadata, query_entries
from .tagging import decide_tag_suggestion, list_tag_suggestions
from .web_common import error_response, media_response, serialize_entry

admin_bp = Blueprint("admin", __name__)


@admin_bp.get("/api/meta")
def api_meta():
    return jsonify(metadata(g.db))


@admin_bp.get("/api/entries")
def api_entries():
    page = max(int_param(request.args, "page", 1), 1)
    page_size = min(max(int_param(request.args, "page_size", 48), 1), 120)
    entries, total = query_entries(g.db, request.args, page, page_size)
    return jsonify(
        {
            "items": [serialize_entry(row) for row in entries],
            "page": page,
            "page_size": page_size,
            "total": total,
        }
    )


@admin_bp.get("/api/entries/<key>")
def api_entry(key: str):
    row = fetch_entry(g.db, key)
    if row is None:
        abort(404)
    item = serialize_entry(row, include_ocr=True)
    item["tag_suggestions"] = list_tag_suggestions(g.db, key, status="pending")
    return jsonify(item)


@admin_bp.post("/api/entries/manual")
def api_create_manual_entry():
    row = management.create_manual_entry(g.db, request.form, request.files.get("image"),
        configured_media_root(current_app, "ATTACHMENT_DIR"), configured_media_root(current_app, "THUMBNAIL_DIR"))
    return jsonify(serialize_entry(row, include_ocr=True)), 201


@admin_bp.patch("/api/entries/<key>")
def api_update_entry(key: str):
    row = management.update_entry(g.db, key, request.get_json(silent=True) or {})
    return jsonify(serialize_entry(row, include_ocr=True))


@admin_bp.get("/api/tags/catalog")
def api_tag_catalog():
    from .tags import tag_catalog
    return jsonify(tag_catalog(g.db))


@admin_bp.put("/api/entries/<key>/tags")
def api_update_tags(key: str):
    row = management.update_tags(g.db, key, request.get_json(silent=True) or {})
    return jsonify(serialize_entry(row, include_ocr=True))


@admin_bp.get("/api/entries/<key>/tag-suggestions")
def api_tag_suggestions(key: str):
    if fetch_entry(g.db, key) is None:
        abort(404)
    status = (request.args.get("status") or "pending").strip()
    try:
        suggestions = list_tag_suggestions(g.db, key, status=status)
    except ValueError as exc:
        return error_response("invalid_request", str(exc), 400)
    return jsonify({"items": suggestions})


@admin_bp.post("/api/tag-suggestions/<int:suggestion_id>/decision")
def api_decide_tag_suggestion(suggestion_id: int):
    data = request.get_json(silent=True) or {}
    try:
        suggestion = decide_tag_suggestion(
            g.db,
            suggestion_id,
            decision=str(data.get("decision") or ""),
            reviewed_by=str(data.get("reviewed_by") or "local_user"),
        )
    except LookupError:
        abort(404)
    except ValueError as exc:
        return error_response("invalid_request", str(exc), 400)
    except RuntimeError as exc:
        return error_response("conflict", str(exc), 409)
    return jsonify(suggestion)


@admin_bp.post("/api/entries/<key>/favorite")
def api_favorite(key: str):
    return jsonify(management.set_favorite(g.db, key, True))


@admin_bp.delete("/api/entries/<key>/favorite")
def api_unfavorite(key: str):
    return jsonify(management.set_favorite(g.db, key, False))


@admin_bp.delete("/api/entries/<key>")
def api_delete_entry(key: str):
    return jsonify(management.delete_entry(g.db, key,
        configured_media_root(current_app, "ATTACHMENT_DIR"), configured_media_root(current_app, "THUMBNAIL_DIR")))


@admin_bp.get("/media/attachments/<attachment_id>")
def media_attachment(attachment_id: str):
    path = media_path(g.db, attachment_id, "local_path")
    if path is None:
        abort(404)
    return media_response(path)


@admin_bp.get("/media/thumbnails/<attachment_id>")
def media_thumbnail(attachment_id: str):
    path = media_path(g.db, attachment_id, "thumbnail_path")
    if path is None:
        abort(404)
    return media_response(path)
