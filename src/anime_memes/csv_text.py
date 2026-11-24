"""Spreadsheet-safe text encoding; review exports identify this format explicitly."""

FORMAT_COLUMN = "_text_encoding"
FORMAT_VERSION = "apostrophe-v1"


def encode_cell(value):
    if not isinstance(value, str) or not value:
        return value
    if value.startswith("'") or value[0] in "\t\r\n" or value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def decode_cell(value: str) -> str:
    if value.startswith("'") and encode_cell(value[1:]) == value:
        return value[1:]
    return value
