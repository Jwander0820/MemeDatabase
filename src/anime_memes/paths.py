"""本機／容器共用媒體路徑解析，不改寫原 DB。"""
import os
from pathlib import Path, PureWindowsPath

from . import settings

ROOT = settings.ROOT

# 存進資料庫的媒體路徑一律用這些相對專案根目錄、正斜線的預設位置，Windows 主機與 Docker 容器都能解析。
MEDIA_DEFAULT_DIRS = {
    "ATTACHMENT": settings.DEFAULT_ATTACHMENT_DIR,
    "THUMBNAIL": settings.DEFAULT_THUMBNAIL_DIR,
    "WEB": settings.DEFAULT_WEB_DIR,
}


def _native(path) -> Path:
    r"""把資料庫或 .env 裡的路徑轉成 Path，反斜線一律視為分隔符號。

    Windows 的 pathlib 本來就把 `\` 當分隔符號；Linux（容器／VPS）則會把它當成檔名的一部分，
    使 `data\attachments\a.png` 變成單一檔名。先統一成正斜線，兩邊解析結果才一致。
    """
    if isinstance(path, Path):
        return path
    return Path(str(path).replace("\\", "/"))


def _absolute(path) -> Path:
    path = _native(path)
    if not (path.drive or path.root):
        path = ROOT / path
    return Path(os.path.normcase(os.path.abspath(path)))


def portable_media_path(kind: str, path) -> str:
    """回傳要寫進資料庫的媒體路徑。

    檔案位於該分類「設定的目錄」（MEMES_{kind}_DIR，例如容器內的 /data/attachments）或預設目錄時，
    一律存成 `data/<分類>/<檔名>`；讀取時由 resolve_local 依環境對應回實際位置。
    直接存實際路徑會因執行環境不同而變成 `/data/attachments/...`（容器）或 `data\\attachments\\...`（Windows），
    換到另一個環境就找不到檔案。不在這兩個目錄的檔案（例如測試用暫存目錄）保留實際路徑。
    """
    default_dir = MEDIA_DEFAULT_DIRS[kind]
    native = _native(path)
    parent = _absolute(native.parent)
    roots = [_absolute(default_dir)]
    configured = settings.configured_media_dir(kind)
    if configured:
        roots.append(_absolute(configured))
    if parent in roots:
        return f"{default_dir}/{native.name}"
    return str(path)


def resolve_local(value, kind=None):
    if not value:
        return None
    path = _native(value)
    native = path if path.is_absolute() else ROOT / path
    # 明確掛載根目錄時，只映射已知媒體分類的 basename。
    # 不回退到任意系統位置，亦拒絕 .. 穿越。
    parts = PureWindowsPath(value).parts
    if ".." in parts:
        return None
    if kind is None:
        if "thumbnails" in parts:
            kind = "THUMBNAIL"
        elif "attachments" in parts:
            kind = "ATTACHMENT"
    configured = settings.configured_media_dir(kind) if kind else None
    if configured:
        base = _native(configured)
        # .env 常用相對路徑（如 data\attachments）；比照 configured_media_root 以專案根目錄為基準，
        # 否則 Flask send_file 會改以套件目錄解析。/data 這類有 root 的容器路徑維持原樣。
        if not (base.drive or base.root):
            base = ROOT / base
        return base / PureWindowsPath(value).name
    return native
