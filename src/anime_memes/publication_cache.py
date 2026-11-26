"""私人發布的可丟棄快取；不寫入管理庫或公開快照。"""
from contextlib import closing
import hashlib
import json
import logging
from pathlib import Path
import sqlite3

from PIL import __version__ as PILLOW_VERSION

log = logging.getLogger(__name__)


def fingerprint(path):
    path = Path(path)
    stat = path.stat()
    return [str(path.resolve()), stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns]


class PublicationCache:
    def __init__(self, db_path, *, force=False):
        self.path = Path(db_path).parent / '.publication-cache.sqlite3'
        self.force = force
        self.rows = {}
        self.pending = {}
        self.signatures = {}
        self.stats = {'decoded': 0, 'validation_reused': 0, 'thumbnails_rendered': 0,
                      'thumbnails_reused': 0}
        try:
            if self.path.is_file():
                with closing(sqlite3.connect(self.path, timeout=2)) as conn:
                    self.rows = {(kind, path): json.loads(value) for kind, path, value in
                                 conn.execute('SELECT kind,path,value FROM media_cache')}
        except (sqlite3.Error, ValueError):
            log.warning('publication_cache_read_failed')

    def get(self, kind, path, signature):
        value = self.rows.get((kind, str(Path(path).resolve())))
        if isinstance(value, dict) and value.get('signature') == signature:
            return value.get('result')
        return None

    def put(self, kind, path, signature, result):
        key = (kind, str(Path(path).resolve()))
        value = {'signature': signature, 'result': result}
        self.rows[key] = self.pending[key] = value

    def source_signature(self, path):
        before = fingerprint(path)
        key = json.dumps(before)
        if key not in self.signatures:
            with Path(path).open('rb') as stream:
                sha = hashlib.file_digest(stream, 'sha256').hexdigest()
            if fingerprint(path) != before:
                raise ValueError('圖片在讀取期間變動，請重試')
            self.signatures[key] = [before, sha]
        return self.signatures[key]

    def decodable(self, path, decode):
        before = self.source_signature(path)
        signature = [1, PILLOW_VERSION, before]
        if not self.force and self.get('decode', path, signature) is True:
            self.stats['validation_reused'] += 1
            return True
        result = decode(path)
        self.stats['decoded'] += 1
        if fingerprint(path) != before[0]:
            raise ValueError('圖片在檢查期間變動，請重試')
        if result:
            self.put('decode', path, signature, True)
        return result

    def thumbnail(self, path, directory, render, preset):
        before = self.source_signature(path)
        signature = [1, PILLOW_VERSION, before, vars(preset)]
        result = self.get('thumb', path, signature)
        if isinstance(result, list) and len(result) == 3:
            sha, width, height = result
            if isinstance(sha, str) and len(sha) == 64 and all(c in '0123456789abcdef' for c in sha):
                target = directory / (sha + '.webp')
                if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == sha:
                    if target.stat().st_mode & 0o044 != 0o044:
                        target.chmod(0o644)
                    self.stats['thumbnails_reused'] += 1
                    return sha, width, height
        data, width, height = render(path, 'thumb')
        if fingerprint(path) != before[0]:
            raise ValueError('圖片在轉檔期間變動，請重試')
        sha = hashlib.sha256(data).hexdigest()
        target = directory / (sha + '.webp')
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != sha:
                raise ValueError('既有媒體內容與雜湊不一致')
            if target.stat().st_mode & 0o044 != 0o044:
                target.chmod(0o644)
        else:
            from .renditions import write_atomic
            # 公開 Nginx (UID 101) 與產圖程序 (UID 10001) 不同；成品需可跨帳號讀取。
            write_atomic(target, data, mode=0o644)
        self.put('thumb', path, signature, [sha, width, height])
        self.stats['thumbnails_rendered'] += 1
        return sha, width, height

    def close(self):
        if not self.pending:
            return
        try:
            with closing(sqlite3.connect(self.path, timeout=2)) as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS media_cache(kind TEXT,path TEXT,value TEXT,PRIMARY KEY(kind,path))')
                conn.executemany('INSERT OR REPLACE INTO media_cache VALUES(?,?,?)',
                                 [(kind, path, json.dumps(value)) for (kind, path), value in self.pending.items()])
                conn.commit()
        except (sqlite3.Error, OSError):
            log.warning('publication_cache_write_failed')
