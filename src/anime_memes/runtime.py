"""獨立服務入口，公開模式不 import 本機管理與 OCR。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="anime-memes-service")
    sub = parser.add_subparsers(dest="command", required=True)
    for mode in ("public", "private"):
        cmd = sub.add_parser(mode)
        cmd.add_argument("--host", default="127.0.0.1")
        cmd.add_argument("--port", type=int, default=8765)
        cmd.add_argument("--threads", type=int, default=4)
    for name in ("validate", "activate", "rollback", "prune"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--root", default=os.environ.get("MEMES_RELEASE_ROOT", "data/releases"))
        if name == "prune":
            cmd.add_argument("--service-stopped", action="store_true")
        else:
            cmd.add_argument("version")
    default_root = os.environ.get("MEMES_RELEASE_ROOT", "data/releases")
    cmd = sub.add_parser("bundle")
    cmd.add_argument("--root", default=default_root)
    cmd.add_argument("--output", required=True)
    cmd.add_argument("version")
    cmd = sub.add_parser("install-bundle")
    cmd.add_argument("--root", default=default_root)
    cmd.add_argument("--activate", action="store_true")
    cmd.add_argument("bundle")
    args = parser.parse_args(argv)
    if args.command in {"public", "private"}:
        from waitress import serve
        if args.command == "public":
            from .public_web import create_public_app
            app = create_public_app()
        else:
            from .web import create_app
            app = create_app(db_path=os.environ.get("MEMES_DB", "data/anime_memes.sqlite3"),
                             attachment_dir=os.environ.get("MEMES_ATTACHMENT_DIR", "data/attachments"),
                             thumbnail_dir=os.environ.get("MEMES_THUMBNAIL_DIR", "data/thumbnails"),
                             release_root=os.environ.get("MEMES_RELEASE_ROOT", "data/releases"))
        serve(app, host=args.host, port=args.port, threads=args.threads)
    elif args.command in {"bundle", "install-bundle"}:
        from .release_bundle import build_bundle, install_bundle
        root = Path(args.root).resolve()
        if args.command == "bundle":
            result = build_bundle(root, args.version, Path(args.output))
        else:
            result = install_bundle(root, Path(args.bundle), activate=args.activate)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        from .releases import activate, validate, prune
        root = Path(args.root).resolve()
        if args.command == "prune":
            result = prune(root, service_stopped=args.service_stopped)
        elif args.command == "validate":
            result = validate(root, args.version)
        else:
            result = activate(root, args.version)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
