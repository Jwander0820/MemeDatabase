from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT_KEYS = ["dateRange", "exportedAt", "messages", "messageCount"]
MESSAGE_KEYS = [
    "id",
    "type",
    "timestamp",
    "timestampEdited",
    "callEndedTimestamp",
    "isPinned",
    "content",
    "author",
    "attachments",
]
AUTHOR_KEYS = ["id", "name", "discriminator", "isBot"]
ATTACHMENT_KEYS = ["id", "url", "fileName", "fileSizeBytes"]


def prune_discord_export(data: dict[str, Any]) -> dict[str, Any]:
    pruned_root = {key: data[key] for key in ROOT_KEYS if key in data}
    messages = pruned_root.get("messages")
    if not isinstance(messages, list):
        return pruned_root

    pruned_messages: list[Any] = []
    for message in messages:
        if not isinstance(message, dict):
            pruned_messages.append(message)
            continue

        pruned_message = {key: message[key] for key in MESSAGE_KEYS if key in message}
        author = pruned_message.get("author")
        if isinstance(author, dict):
            pruned_message["author"] = {
                key: author[key] for key in AUTHOR_KEYS if key in author
            }

        attachments = pruned_message.get("attachments")
        if isinstance(attachments, list):
            pruned_attachments: list[Any] = []
            for attachment in attachments:
                if isinstance(attachment, dict):
                    pruned_attachments.append(
                        {
                            key: attachment[key]
                            for key in ATTACHMENT_KEYS
                            if key in attachment
                        }
                    )
                else:
                    pruned_attachments.append(attachment)
            pruned_message["attachments"] = pruned_attachments

        pruned_messages.append(pruned_message)

    pruned_root["messages"] = pruned_messages
    pruned_root["messageCount"] = len(pruned_messages)
    return pruned_root


def convert_file(input_path: str | Path, output_path: str | Path | None = None) -> Path:
    input_file = Path(input_path)
    if output_path is None:
        output_file = input_file.with_name(f"{input_file.stem}.fixed{input_file.suffix}")
    else:
        output_file = Path(output_path)

    data = json.loads(input_file.read_text(encoding="utf-8"))
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(prune_discord_export(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_file
