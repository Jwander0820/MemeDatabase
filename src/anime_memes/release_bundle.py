"""發布包（tar.gz）的打包與安裝；只依賴標準庫與 releases，會進公開映像。"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import tarfile
import uuid

from .releases import VERSION_RE, activate as activate_release, digest, validate

_VERSION_FILE_RE = re.compile(r"versions/([^/]+)/(manifest\.json|public\.sqlite3)\Z")
_MEDIA_RE = re.compile(r"media/([0-9a-f]{64}\.webp)\Z")
# 解壓總量上限：遠大於目前館藏（千筆縮圖約數十 MB），只為擋下異常或惡意的壓縮包塞滿磁碟。
MAX_EXTRACTED_BYTES = 4 * 1024 ** 3


def build_bundle(root: Path, version: str, output: Path) -> dict:
    root = Path(root)
    output = Path(output)
    manifest = validate(root, version)
    folder = root / "versions" / version
    members = [(folder / "manifest.json", f"versions/{version}/manifest.json"),
               (folder / "public.sqlite3", f"versions/{version}/public.sqlite3")]
    members += [(root / "media" / name, f"media/{name}") for name in sorted(manifest["media"])]
    with tarfile.open(output, "w:gz") as tar:
        for source, arcname in members:
            tar.add(source, arcname=arcname, recursive=False)
    return {"version": version, "count": manifest["count"], "media": len(manifest["media"]),
            "bytes": output.stat().st_size}


def _check_members(tar: tarfile.TarFile) -> tuple[str, list[tarfile.TarInfo]]:
    """寫入任何檔案前先檢查全部成員；回傳版本名稱與成員清單。"""
    members = tar.getmembers()
    seen: set[str] = set()
    versions: set[str] = set()
    version_files: set[str] = set()
    for member in members:
        name = member.name
        if not member.isreg():
            raise ValueError(f"發布包成員不是一般檔案：{name}")
        if name in seen:
            raise ValueError(f"發布包成員重複：{name}")
        seen.add(name)
        if "\\" in name or name.startswith("/") or ".." in name.split("/"):
            raise ValueError(f"發布包成員路徑不安全：{name}")
        match = _VERSION_FILE_RE.fullmatch(name)
        if match:
            if not VERSION_RE.fullmatch(match.group(1)):
                raise ValueError(f"發布包版本名稱無效：{name}")
            versions.add(match.group(1))
            version_files.add(match.group(2))
        elif not _MEDIA_RE.fullmatch(name):
            raise ValueError(f"發布包成員不在白名單：{name}")
    if sum(member.size for member in members) > MAX_EXTRACTED_BYTES:
        raise ValueError("發布包解壓後超過大小上限")
    if len(versions) != 1:
        raise ValueError("發布包必須恰好包含一個版本")
    if version_files != {"manifest.json", "public.sqlite3"}:
        raise ValueError("發布包缺少 manifest.json 或 public.sqlite3")
    return next(iter(versions)), members


def _extract(tar: tarfile.TarFile, member: tarfile.TarInfo, target: Path) -> None:
    source = tar.extractfile(member)
    if source is None:
        raise ValueError(f"無法讀取發布包成員：{member.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with source, target.open("xb") as handle:
        shutil.copyfileobj(source, handle, 1024 * 1024)


def install_bundle(root: Path, bundle: Path, *, activate: bool = False) -> dict:
    root = Path(root)
    with tarfile.open(bundle, "r:gz") as tar:
        version, members = _check_members(tar)
        if (root / "versions" / version).exists() or (root / "versions" / version).is_symlink():
            raise ValueError(f"版本已存在：{version}")
        incoming = root / (".incoming-" + uuid.uuid4().hex)
        try:
            for member in members:
                _extract(tar, member, incoming / member.name)
            media_added = media_existing = 0
            for path in sorted((incoming / "media").glob("*.webp")) if (incoming / "media").is_dir() else []:
                target = root / "media" / path.name
                if target.exists() or target.is_symlink():
                    if target.is_symlink() or digest(target) != path.name[:-5]:
                        raise ValueError(f"既有媒體雜湊與檔名不符：{path.name}")
                    media_existing += 1
                else:
                    if digest(path) != path.name[:-5]:
                        raise ValueError(f"媒體雜湊與檔名不符：{path.name}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(path, target)
                    media_added += 1
            (root / "versions").mkdir(parents=True, exist_ok=True)
            final = root / "versions" / version
            os.replace(incoming / "versions" / version, final)
            try:
                manifest = validate(root, version)
            except BaseException:
                shutil.rmtree(final, ignore_errors=True)
                raise
            if activate:
                activate_release(root, version)
        finally:
            shutil.rmtree(incoming, ignore_errors=True)
    return {"version": version, "count": manifest["count"], "media_added": media_added,
            "media_existing": media_existing, "activated": bool(activate)}
