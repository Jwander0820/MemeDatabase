"""私人發布的可丟棄快取；不寫入管理庫或公開快照。"""
from contextlib import closing
import hashlib
import json
import logging
from pathlib import Path
import sqlite3

from PIL import __version__ as PILLOW_VERSION

log = logging.getLogger(__name__)


# 快取簽章格式；改動簽章結構時遞增，讓舊紀錄自然失效。
CACHE_FORMAT = 2


def fingerprint(path):
    """當次檔案指紋，只用來偵測讀取／轉檔期間檔案是否被改動。

    st_dev、st_ino、st_ctime_ns 與絕對路徑會隨容器重建、掛載或主機/容器切換而改變，
    原圖內容卻沒變，因此不可寫入持久快取的簽章；持久簽章一律用 content 簽章。
    """
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

    def _lookup(self, kind, key, signature):
        value = self.rows.get((kind, key))
        if isinstance(value, dict) and value.get('signature') == signature:
            return value.get('result')
        return None

    def _store(self, kind, key, signature, result):
        value = {'signature': signature, 'result': result}
        self.rows[(kind, key)] = self.pending[(kind, key)] = value

    def get(self, kind, path, signature):
        """以來源路徑為鍵；用於結果綁定特定附件的項目（例如 720P）。"""
        return self._lookup(kind, str(Path(path).resolve()), signature)

    def put(self, kind, path, signature, result):
        self._store(kind, str(Path(path).resolve()), signature, result)

    def observe(self, path):
        """回傳 (當次指紋, 內容簽章)。

        指紋只拿來比對本次處理期間檔案有沒有變；內容簽章 [大小, SHA-256] 才可寫入快取，
        所以重建容器、重新掛載或換路徑都不會讓快取失效，內容被替換則一定失效。
        """
        current = fingerprint(path)
        key = json.dumps(current)
        if key not in self.signatures:
            with Path(path).open('rb') as stream:
                sha = hashlib.file_digest(stream, 'sha256').hexdigest()
            if fingerprint(path) != current:
                raise ValueError('圖片在讀取期間變動，請重試')
            self.signatures[key] = [current[3], sha]
        return current, self.signatures[key]

    def decodable(self, path, decode):
        before, content = self.observe(path)
        # 解碼結果只取決於內容，以內容為鍵，主機與容器路徑不同也能共用。
        key, signature = 'sha256:' + content[1], [CACHE_FORMAT, PILLOW_VERSION, content]
        if not self.force and self._lookup('decode', key, signature) is True:
            self.stats['validation_reused'] += 1
            return True
        result = decode(path)
        self.stats['decoded'] += 1
        if fingerprint(path) != before:
            raise ValueError('圖片在檢查期間變動，請重試')
        if result:
            self._store('decode', key, signature, True)
        return result

    def thumbnail(self, path, directory, render, preset):
        before, content = self.observe(path)
        key, signature = 'sha256:' + content[1], [CACHE_FORMAT, PILLOW_VERSION, content, vars(preset)]
        result = self._lookup('thumb', key, signature)
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
        if fingerprint(path) != before:
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
        self._store('thumb', key, signature, [sha, width, height])
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
