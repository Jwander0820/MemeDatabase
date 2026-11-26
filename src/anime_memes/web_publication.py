from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Blueprint, abort, current_app, g, jsonify, request

from .web_common import error_response

publication_bp = Blueprint("publication", __name__)


@publication_bp.get('/api/publication/vps')
def vps_status():
    response = jsonify(current_app.extensions['vps_publication'].snapshot())
    response.headers['Cache-Control'] = 'no-store'
    return response


@publication_bp.put('/api/publication/vps/config')
def vps_config():
    return jsonify(current_app.extensions['vps_publication'].save_config(request.get_json(silent=True)))


@publication_bp.post('/api/publication/vps')
def vps_start():
    return jsonify(current_app.extensions['vps_publication'].start(request.get_json(silent=True))), 202


@publication_bp.route("/api/entries/<key>/publication", methods=["GET", "PATCH"])
def publication(key):
    from .publishing import selection, update_selection
    try:
        result = selection(g.db, key) if request.method == "GET" else update_selection(g.db, key, request.get_json())
    except KeyError:
        abort(404)
    return jsonify(result)


@publication_bp.post("/api/publication/bulk")
def publication_bulk():
    from .publishing import bulk_update_selection
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("請求內容必須為 JSON 物件")
    unknown = set(data) - {"publish_selected", "keys", "filter", "dry_run"}
    if unknown:
        raise ValueError(f"不支援的欄位：{', '.join(sorted(unknown))}")
    if "publish_selected" not in data:
        raise ValueError("缺少 publish_selected")
    return jsonify(bulk_update_selection(
        g.db, selected=data["publish_selected"], keys=data.get("keys"),
        filters=data.get("filter"), dry_run=data.get("dry_run", False)))


@publication_bp.post("/api/publication/check")
def publication_check():
    from .publishing import check_database
    return jsonify(check_database(current_app.config["DB_PATH"]))


@publication_bp.post("/api/publication/export")
def publication_export():
    from .publishing import export_release
    if request.get_data() and request.get_json(silent=True) != {}:
        raise ValueError("匯出不接受路徑或其他參數")
    result = export_release(current_app.config["DB_PATH"], current_app.config["RELEASE_ROOT"])
    if not result["exported"]:
        # 保留 exported／report 等欄位，另附統一格式的 error。
        return jsonify({**result, "error": {"code": "export_failed", "message": "發布前檢查未通過，未匯出"}}), 422
    return jsonify(result)


@publication_bp.post("/api/publication/activate")
def publication_activate():
    from .releases import activate
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"version"}:
        raise ValueError("啟用只接受 version")
    try:
        manifest = activate(Path(current_app.config["RELEASE_ROOT"]), data["version"])
    except (OSError, sqlite3.Error, KeyError, TypeError, AttributeError):
        return error_response("invalid_release", "無法驗證或寫入發布包，請確認檔案完整及目錄權限", 422)
    return jsonify(version=manifest["version"], count=manifest["count"])


@publication_bp.post("/api/publication/purge")
def publication_purge():
    from .publishing import purge_releases
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - {"dry_run", "keep_recent"}:
        raise ValueError("清除只接受 dry_run 與 keep_recent")
    return jsonify(purge_releases(current_app.config["DB_PATH"], current_app.config["RELEASE_ROOT"],
                                  dry_run=data.get("dry_run", True), keep_recent=data.get("keep_recent", 0)))
