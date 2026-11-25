import ast
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "anime_memes"

# runtime.py 在 private 模式才 lazy import web（含本機管理與 OCR 相關相依），
# 它只屬於私人映像，公開映像不該、也不會有這個檔案，所以計算閉包時跳過這條邊。
PRIVATE_ONLY_FROM_RUNTIME = {"web"}


def base_stage_modules(dockerfile_text):
    """解析 Dockerfile 的 base 階段，回傳 COPY 進 src/anime_memes/ 的模組名集合。"""
    text = dockerfile_text.replace("\\\r\n", " ").replace("\\\n", " ")
    modules = set()
    in_base = False
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"FROM\s+\S+(?:\s+AS\s+(\S+))?", line, re.IGNORECASE)
        if m:
            in_base = (m.group(1) or "").lower() == "base"
            continue
        if in_base and re.match(r"COPY\s", line, re.IGNORECASE):
            for token in line.split()[1:]:
                tm = re.fullmatch(r"src/anime_memes/([A-Za-z0-9_]+)\.py", token)
                if tm:
                    modules.add(tm.group(1))
    return modules


def relative_imports(path):
    """回傳該檔（含函式內）所有相對匯入的模組名集合。"""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module:
                found.add(node.module.split(".")[0])
            else:  # from . import x，x 是模組名
                found.update(alias.name for alias in node.names)
    return found


def runtime_closure(pkg_dir=PKG):
    seen, todo = set(), ["runtime"]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        for dep in relative_imports(pkg_dir / f"{name}.py"):
            if name == "runtime" and dep in PRIVATE_ONLY_FROM_RUNTIME:
                continue
            todo.append(dep)
    return seen


def diff_message(closure, copied):
    return (f"Dockerfile 缺少：{sorted(closure - copied)}；"
            f"Dockerfile 多出：{sorted(copied - closure)}")


class PublicImageTest(unittest.TestCase):
    def setUp(self):
        self.copied = base_stage_modules((ROOT / "Dockerfile").read_text(encoding="utf-8"))

    def test_dockerfile_matches_runtime_closure(self):
        closure = runtime_closure()
        self.assertEqual(closure, self.copied, diff_message(closure, self.copied))

    def test_parser_detects_missing_and_extra(self):
        fake = ("FROM python:3.12-slim AS base\n"
                "COPY src/anime_memes/text.py \\\n  src/anime_memes/runtime.py ./src/anime_memes/\n"
                "FROM base AS private\nCOPY src/anime_memes/web.py ./src/anime_memes/\n")
        self.assertEqual(base_stage_modules(fake), {"text", "runtime"})
        closure = runtime_closure()
        self.assertIn("urls", closure)
        self.assertNotEqual(closure, base_stage_modules(fake))
        self.assertIn("urls", diff_message(closure, base_stage_modules(fake)))

    def test_public_image_imports_work_in_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "src" / "anime_memes"
            dest.mkdir(parents=True)
            (Path(tmp) / "src" / "__init__.py").write_text("", encoding="utf-8")
            (dest / "__init__.py").write_text("", encoding="utf-8")
            for name in self.copied:
                shutil.copy(PKG / f"{name}.py", dest / f"{name}.py")
            code = (
                "import sys, importlib\n"
                f"sys.path.insert(0, {tmp!r})\n"
                "import src.anime_memes.runtime, src.anime_memes.public_web, src.anime_memes.releases\n"
                f"for n in {sorted(self.copied)!r}:\n"
                "    importlib.import_module('src.anime_memes.' + n)\n"
            )
            proc = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
