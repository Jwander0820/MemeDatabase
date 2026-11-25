"""本機／容器共用媒體路徑解析，不改寫原 DB。"""
from pathlib import Path, PureWindowsPath
import os

from . import settings

ROOT = settings.ROOT


def resolve_local(value, kind=None):
    if not value:
        return None
    path = Path(value)
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
    configured = os.environ.get(f"MEMES_{kind}_DIR") if kind else None
    if configured:
        base = Path(configured)
        # .env 常用相對路徑（如 data\attachments）；比照 configured_media_root 以專案根目錄為基準，
        # 否則 Flask send_file 會改以套件目錄解析。/data 這類有 root 的容器路徑維持原樣。
        if not (base.drive or base.root):
            base = ROOT / base
        return base / PureWindowsPath(value).name
    return native
