from __future__ import annotations

import sqlite3
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
    # DiscordChatExporter treats --before as an exclusive boundary.  Using
    # tomorrow keeps messages posted today in a scheduled export.
    return (datetime.now(timezone.utc).astimezone() + timedelta(days=1)).date().isoformat()


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


def incremental_after(conn: sqlite3.Connection, channel_id: str) -> str | None:
    """Return a one-day-overlap cursor, including legacy databases without a run cursor."""
    state = conn.execute(
        "SELECT covered_through, overlap_days FROM discord_sync_state WHERE channel_id = ?",
        (channel_id,),
    ).fetchone()
    if state and state["covered_through"]:
        covered = str(state["covered_through"])
        try:
            parsed = datetime.fromisoformat(covered.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.fromisoformat(f"{covered}T00:00:00")
        return (parsed - timedelta(days=int(state["overlap_days"] or 1))).date().isoformat()

    row = conn.execute(
        """
        SELECT before_value
        FROM export_runs
        WHERE status = 'success' AND channel_id = ? AND before_value IS NOT NULL
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (channel_id,),
    ).fetchone()
    if row:
        return overlap_after(row["before_value"])

    row = conn.execute(
        "SELECT MAX(timestamp) AS latest FROM discord_messages WHERE COALESCE(type, '') != 'Manual'"
    ).fetchone()
    latest = row["latest"] if row else None
    if not latest:
        return None
    try:
        parsed = datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed - timedelta(days=1)).date().isoformat()


def save_sync_state(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    export_run_id: int,
    after_value: str | None,
    covered_through: str,
    last_message_timestamp: str | None,
    last_message_id: str | None,
    overlap_days: int = 1,
) -> None:
    """Advance the durable cursor after the complete sync workflow succeeds."""
    previous = conn.execute(
        "SELECT * FROM discord_sync_state WHERE channel_id = ?",
        (channel_id,),
    ).fetchone()
    if previous:
        if str(previous["covered_through"] or "") > covered_through:
            covered_through = str(previous["covered_through"])
        previous_timestamp = previous["last_message_timestamp"]
        if previous_timestamp and (not last_message_timestamp or str(previous_timestamp) > last_message_timestamp):
            last_message_timestamp = str(previous_timestamp)
            last_message_id = previous["last_message_id"]

    conn.execute(
        """
        INSERT INTO discord_sync_state(
            channel_id, last_export_run_id, last_after_value, covered_through,
            last_message_timestamp, last_message_id, overlap_days, last_success_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(channel_id) DO UPDATE SET
            last_export_run_id=excluded.last_export_run_id,
            last_after_value=excluded.last_after_value,
            covered_through=excluded.covered_through,
            last_message_timestamp=excluded.last_message_timestamp,
            last_message_id=excluded.last_message_id,
            overlap_days=excluded.overlap_days,
            last_success_at=CURRENT_TIMESTAMP,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            channel_id,
            export_run_id,
            after_value,
            covered_through,
            last_message_timestamp,
            last_message_id,
            overlap_days,
        ),
    )
    conn.commit()


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
