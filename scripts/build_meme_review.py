"""將梗圖候選 JSONL 製成獨立的本機審核頁。"""

import argparse
import hashlib
import json
import re
from pathlib import Path


def read_rows(path):
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if line.strip():
            try:
                row = json.loads(line)
                if not isinstance(row.get("tags"), list):
                    raise ValueError("需提供標籤陣列")
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"{path} 第 {number} 行：{exc}") from exc
            rows.append(row)
    return rows


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", r"\u003c")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("output", type=Path, nargs="?",
                        default=Path(__file__).resolve().parents[1] / "data/web-meme-import/review.html")
    parser.add_argument("--approved", type=Path)
    parser.add_argument("--local-root", type=Path)
    args = parser.parse_args()
    rows = read_rows(args.candidates)
    approved = read_rows(args.approved) if args.approved else []
    def key_for(row):
        parts = [row.get("source_page"), row.get("image_url")]
        if row.get("local_path"):
            parts.append(row.get("local_path"))
        return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))

    keys = [key_for(row) for row in rows]
    if len(keys) != len(set(keys)):
        parser.error("同批有重複的來源與圖址組合")
    template = Path(__file__).resolve().parents[1] / "docs/meme-review-template.html"
    originals = dict(zip(keys, rows))
    selected = []
    edits = {}
    for row in approved:
        key = row.get("review_candidate_key", key_for(row))
        if key not in originals or key in selected:
            parser.error("核准檔有重複或不屬於本批的資料")
        selected.append(key)
        edits[key] = {f: row[f] for f in ("source_page", "caption", "subject", "display_title", "speaker", "tags")
                      if f in row and row[f] != originals[key].get(f)}
    local_root_uri = args.local_root.resolve().as_uri().rstrip("/") + "/" if args.local_root else ""
    values = {
        "__BATCH_ITEMS__": encode(rows),
        "__APPROVED_KEYS__": encode(selected),
        "__INITIAL_EDITS__": encode(edits),
        "__BATCH_ID__": hashlib.sha256(encode(keys).encode()).hexdigest()[:20],
        "__LOCAL_ROOT_URI__": encode(local_root_uri),
    }
    page = re.sub("|".join(values), lambda m: values[m.group()], template.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
