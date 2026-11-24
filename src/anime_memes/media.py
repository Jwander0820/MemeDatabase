"""本機媒體路徑與受限刪除操作。"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any
from .paths import resolve_local
ROOT = Path(__file__).resolve().parents[2]

def media_path(conn: sqlite3.Connection, attachment_id: str, column: str) -> Path | None:
    if column not in {"local_path", "thumbnail_path"}:
        return None
    row = conn.execute(f"SELECT {column} FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
    if not row or not row[column]:
        return None
    path = resolve_local(row[column], "THUMBNAIL" if column == "thumbnail_path" else "ATTACHMENT")
    if path is None:
        return None
    if not path.exists() or path.stat().st_size <= 0:
        return None
    return path


def resolve_media_path(value: str | None) -> Path | None:
    return resolve_local(value)


def configured_media_root(app: Any, key: str) -> Path:
    path = Path(str(app.config[key]))
    if not path.is_absolute():
        path = ROOT / path
    return path


class DeleteFileResult:
    def __init__(self, *, path: str | None = None, deleted_path: str | None = None, error: str | None = None) -> None:
        self.path = path
        self.deleted_path = deleted_path
        self.error = error


def safe_delete_media_file(path: Path | None, allowed_root: Path) -> DeleteFileResult:
    if path is None:
        return DeleteFileResult()
    try:
        resolved = path.resolve()
        root = allowed_root.resolve()
        if not is_relative_to(resolved, root):
            return DeleteFileResult(path=str(resolved), error="path outside media root")
        if not resolved.exists():
            return DeleteFileResult(path=str(resolved))
        if not resolved.is_file():
            return DeleteFileResult(path=str(resolved), error="not a file")
        resolved.unlink()
        return DeleteFileResult(path=str(resolved), deleted_path=str(resolved))
    except OSError as exc:
        return DeleteFileResult(path=str(path), error=str(exc))


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def file_exists(path: Path | None) -> bool:
    return path is not None and path.exists() and path.stat().st_size > 0
