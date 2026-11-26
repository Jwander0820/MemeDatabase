"""選用的真實 Docker 驗收：RUN_CONTAINER_TESTS=1；不使用正式館藏。"""
from contextlib import closing
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid

from PIL import Image

from src.anime_memes.db import connect, init_db
from src.anime_memes.publishing import export_release, update_selection
from src.anime_memes.r2 import R2_ENV_VARS
from src.anime_memes.releases import activate


@unittest.skipUnless(os.environ.get("RUN_CONTAINER_TESTS") == "1", "set RUN_CONTAINER_TESTS=1 for real Docker integration")
class ContainerTest(unittest.TestCase):
    def test_full_profiles_and_hot_publication(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="anime-containers-") as tmp:
            root = Path(tmp)
            for folder in ["attachments", "thumbnails", "deploy-ssh"]:
                (root / folder).mkdir()
            image = root / "thumbnails" / "a.webp"
            Image.new("RGB", (420, 315), "blue").save(image)
            Image.new("RGB", (800, 600), "blue").save(root / "attachments" / "a.png")
            db = root / "anime_memes.sqlite3"
            with closing(connect(db)) as conn:
                init_db(conn)
                conn.execute("INSERT INTO discord_messages(id,timestamp) VALUES('m','2026-11-24')")
                conn.execute("INSERT INTO attachments(id,message_id,file_name,local_path,thumbnail_path) VALUES('a','m','a.png',?,?)",
                             (str(root / "attachments/a.png"), str(image)))
                conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,pairing_method,confidence,status)
                    VALUES('meme_a','a','m','container test','test',1,'ready')""")
                conn.commit()
                update_selection(conn, "meme_a", {"publish_selected": True})
            releases = root / "releases"
            old = export_release(db, releases, store=None)["version"]
            activate(releases, old)
            env = {**os.environ, "PRIVATE_DATA_ROOT": str(root), "RELEASE_ROOT": str(releases),
                   "PRIVATE_PORT": "18865", "PUBLIC_PORT": "18875", "PRIVATE_URL": "http://127.0.0.1:18865",
                   "MEMES_MCP_TOKEN": secrets.token_hex(24),
                   # 明確清空 R2 變數：shell 值優先於 compose 讀到的 .env，避免整合測試真的上傳。
                   **{name: "" for name in R2_ENV_VARS}}
            project = "memedatabase-test-" + uuid.uuid4().hex[:8]
            compose = ["docker", "compose", "-p", project, "--profile", "public", "--profile", "private", "--profile", "mcp"]

            def docker(*args):
                result = subprocess.run([*compose, *args], cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
                self.assertEqual(result.returncode, 0, result.stdout[-1500:] + result.stderr[-1500:])
                return result.stdout

            def http(path, *, private=False, method="GET", data=None):
                base = "http://127.0.0.1:18865" if private else "http://127.0.0.1:18875"
                headers = {"Content-Type": "application/json"} if data is not None else {}
                req = urllib.request.Request(base + path, method=method, data=json.dumps(data).encode() if data is not None else None, headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        return r.status, dict(r.headers), r.read()
                except urllib.error.HTTPError as exc:
                    return exc.code, dict(exc.headers), exc.read()

            try:
                docker("up", "-d", "--build", "--wait", "--wait-timeout", "90")
                docker("exec", "-T", "public-api", "python", "-c",
                       "import importlib.util,pathlib; assert all(importlib.util.find_spec(m) is None for m in ['PIL','mcp','paddle','paddleocr','torch']); assert not pathlib.Path('/app/src/anime_memes/web.py').exists()")
                docker('exec', '-T', 'private-api', 'python', '-c',
                       "import shutil; assert shutil.which('ssh') and shutil.which('scp')")
                inventory = json.loads(docker('exec', '-T', 'public-api', 'python', '-m',
                                              'src.anime_memes.runtime', 'media-inventory', '--root', '/releases'))
                self.assertTrue(inventory['media'])
                # 公開映像沒有 Pillow，仍應能打包、安裝只有 DB／manifest 的增量包。
                docker('exec', '-T', 'public-api', 'python', '-c',
                       "from pathlib import Path; import json,os,shutil,tempfile,tarfile; "
                       "from src.anime_memes.release_bundle import build_bundle,install_bundle,media_inventory; "
                       "r=Path('/releases'); v=json.loads((r/'current.json').read_text())['version']; "
                       "t=tempfile.TemporaryDirectory(); p=Path(t.name); target=p/'target'; "
                       "os.umask(0o077); (target/'media').mkdir(parents=True); "
                       "inventory=media_inventory(r); "
                       "[shutil.copyfile(r/'media'/name,target/'media'/name) for name in inventory['media']]; "
                       "b=p/'delta.tar.gz'; result=build_bundle(r,v,b,inventory=inventory); assert result['media']==0; "
                       "install_bundle(target,b,activate=True); "
                       "assert (target/'media').stat().st_mode & 0o777 == 0o755; "
                       "assert all(path.stat().st_mode & 0o777 == 0o644 for path in (target/'media').iterdir()); t.cleanup()")
                docker("exec", "-T", "public-api", "python", "-c",
                       "import pathlib; p=pathlib.Path('/releases/write-test');\ntry: p.write_text('bad')\nexcept OSError: pass\nelse: raise AssertionError('mount writable')")
                docker("exec", "-T", "public-nginx", "nginx", "-t")
                self.assertEqual(http("/health/ready")[0], 200)
                self.assertIn("404 Not Found", docker("exec", "-T", "public-nginx", "sh", "-c",
                    "wget -O /dev/null http://127.0.0.1:8080/health/ready 2>&1 || true"))
                for svc in ("public-nginx", "private-nginx"):
                    # 非 root 且丟棄所有 capability（CapEff 全 0），根檔案系統唯讀。
                    docker("exec", "-T", svc, "sh", "-c",
                           "test \"$(id -u)\" != 0 && grep -q '^CapEff:[[:space:]]*0000000000000000$' /proc/self/status && ! touch /etc/nginx/probe 2>/dev/null")
                docker("exec", "-T", "public-nginx", "sh", "-c", "test ! -e /releases && test ! -e /usr/share/nginx/admin/index.html")
                first = json.loads(http("/api/v1/entries")[2])
                self.assertEqual(first["version"], old)
                status, headers, body = http(first["items"][0]["thumbnail_url"])
                self.assertEqual(status, 200)
                self.assertIn("immutable", headers["Cache-Control"])
                with Image.open(BytesIO(body)) as im:
                    # 公開縮圖由本地原圖（800x600）產生 480P，不可超過 640x480。
                    self.assertLessEqual(im.width, 640)
                    self.assertLessEqual(im.height, 480)
                self.assertIn("/assets/gallery.js", http("/")[2].decode())
                # 安全標頭：頁面、靜態檔與 API 都要有，API 的 nosniff 不可重複。
                page_headers = http("/")[1]
                self.assertIn("frame-ancestors 'none'", page_headers["Content-Security-Policy"])
                self.assertEqual(page_headers["X-Frame-Options"], "DENY")
                self.assertIn("Content-Security-Policy", http("/assets/gallery.js")[1])
                self.assertEqual(http("/api/v1/entries")[1]["X-Content-Type-Options"], "nosniff")
                self.assertIn("Content-Security-Policy", http("/", private=True)[1])
                # 私人管理端擋外部 Host（DNS rebinding）。
                rebinding = urllib.request.Request("http://127.0.0.1:18865/api/entries", headers={"Host": "evil.example:18865"})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(rebinding, timeout=10)
                self.assertEqual(caught.exception.code, 403)
                for path in ["/assets/gallery.js", "/assets/gallery.css", "/assets/catalog.css"]:
                    self.assertEqual(http(path)[0], 200)
                self.assertEqual(http("/assets/catalog.css")[2], http("/static/catalog.css", private=True)[2])
                for path in ["/mcp", "/api/entries", "/public.sqlite3", "/static/app.js",
                             "/api/publication/activate", "/static/publication.js",
                             f"/reports/{old}.json", f"/versions/{old}/public.sqlite3"]:
                    self.assertEqual(http(path)[0], 404)
                self.assertEqual(http("/api/v1/entries", method="POST", data={})[0], 405)
                self.assertEqual(http("/", private=True)[0], 200)
                vps_status = json.loads(http('/api/publication/vps', private=True)[2])
                self.assertTrue(vps_status['ssh_available'])
                self.assertFalse(vps_status['ssh_ready'])
                self.assertEqual(http('/api/publication/vps')[0], 404)
                item = json.loads(http("/api/entries", private=True)[2])["items"][0]
                self.assertTrue(item["has_local_image"])
                self.assertEqual(http(item["thumbnail_url"], private=True)[0], 200)
                self.assertEqual(http(item["attachment_url"], private=True)[0], 200)
                self.assertEqual(http("/api/entries/meme_a", private=True, method="PATCH", data={"quote_text": "new container version"})[0], 200)
                # 由 Linux 私人容器重新產圖，驗證 UID 101 能讀 UID 10001 的公開成品。
                docker("exec", "-T", "private-api", "python", "-c",
                       "from PIL import Image; Image.new('RGB',(800,600),'red').save('/data/attachments/a.png')")
                result = json.loads(http("/api/publication/export", private=True, method="POST", data={})[2])
                self.assertTrue(result["exported"])
                self.assertEqual(result["report"]["media_validation"]["thumbnails_rendered"], 1)
                self.assertEqual(json.loads(http("/api/v1/entries")[2])["version"], old)
                self.assertEqual(http("/api/publication/activate", private=True, method="POST",
                                      data={"version": result["version"]})[0], 200)
                time.sleep(0.6)
                self.assertEqual(json.loads(http("/api/v1/entries")[2])["items"][0]["quote_text"], "new container version")
                new_item = json.loads(http("/api/v1/entries")[2])["items"][0]
                self.assertEqual(http(new_item["thumbnail_url"])[0], 200)
                time.sleep(0.6)
                self.assertEqual(http("/api/v1/entries?version=" + old)[0], 410)
                docker("exec", "-T", "private-api", "python", "-m", "src.anime_memes.runtime", "rollback", "--root", "/releases", old)
                time.sleep(0.6)
                self.assertEqual(json.loads(http("/api/v1/entries")[2])["version"], old)
                statuses = [http("/api/v1/entries")[0] for _ in range(25)]
                self.assertIn(429, statuses)
                docker("up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90", "public-api")
                time.sleep(0.6)
                self.assertEqual(json.loads(http("/api/v1/entries")[2])["version"], old)
                # 驗證私人 MCP 真正經過容器 HTTP 協定。
                mcp_url = "http://127.0.0.1:8766/mcp"
                def rpc(method, params, headers=None):
                    defaults = {"Authorization": "Bearer " + env["MEMES_MCP_TOKEN"], "Content-Type": "application/json",
                                "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
                    req = urllib.request.Request(mcp_url, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(), headers=headers if headers is not None else defaults)
                    return urllib.request.urlopen(req, timeout=10)
                with self.assertRaises(urllib.error.HTTPError) as exc:
                    rpc("tools/list", {}, {})
                self.assertEqual(exc.exception.code, 401)
                with rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "docker-test", "version": "1"}}) as r:
                    self.assertIn("result", json.load(r))
                with rpc("tools/call", {"name": "search_memes", "arguments": {"query": "container", "limit": 1}}) as r:
                    self.assertIn("new container version", str(json.load(r)))
            finally:
                docker("down", "--remove-orphans")
