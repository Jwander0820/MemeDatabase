from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .convert import convert_file


def timestamp_for_filename(value: datetime | None = None) -> str:
    return (value or datetime.now()).strftime("%Y%m%d_%H%M%S")


def build_export_path(channel_id: str, output_dir: str | Path, after: str | None, before: str | None) -> Path:
    safe_after = (after or "start").replace(":", "").replace("/", "-").replace("\\", "-")
    safe_before = (before or "now").replace(":", "").replace("/", "-").replace("\\", "-")
    name = f"channel_{channel_id}_{timestamp_for_filename()}__after_{safe_after}__before_{safe_before}.raw.json"
    return Path(output_dir) / name


def run_discord_export(
    *,
    exporter_exe: str | Path,
    token: str,
    channel_id: str,
    output_path: str | Path,
    after: str | None = None,
    before: str | None = None,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(exporter_exe),
        "export",
        "-t",
        token,
        "-c",
        channel_id,
        "-f",
        "Json",
        "-o",
        str(output),
    ]
    if after:
        command.extend(["--after", after])
    if before:
        command.extend(["--before", before])
    subprocess.run(command, check=True)
    return output


def default_before() -> str:
    return datetime.now(timezone.utc).astimezone().date().isoformat()


def overlap_after(last_before: str | None) -> str | None:
    if not last_before:
        return None
    try:
        parsed = datetime.fromisoformat(last_before.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{last_before}T00:00:00")
        except ValueError:
            return last_before
    return (parsed - timedelta(days=1)).date().isoformat()


def export_and_convert(
    *,
    exporter_exe: str | Path,
    token: str,
    channel_id: str,
    raw_dir: str | Path,
    fixed_dir: str | Path,
    after: str | None,
    before: str | None,
) -> tuple[Path, Path]:
    raw_path = build_export_path(channel_id, raw_dir, after, before)
    run_discord_export(
        exporter_exe=exporter_exe,
        token=token,
        channel_id=channel_id,
        output_path=raw_path,
        after=after,
        before=before,
    )
    fixed_path = Path(fixed_dir) / raw_path.name.replace(".raw.json", ".fixed.json")
    return raw_path, convert_file(raw_path, fixed_path)
