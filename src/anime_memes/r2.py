"""Cloudflare R2（S3 相容 API）最小上傳客戶端。

只用標準函式庫實作 AWS Signature V4，避免為了 HEAD／PUT 兩個動作引入 boto3。
物件鍵使用內容雜湊，內容不變就不重傳；同一鍵永遠對應同一內容，可安全設定永久快取。
錯誤訊息不得包含金鑰、簽章或 Authorization 標頭。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Mapping
from urllib.parse import quote

from .urls import original_url

ENV_NAMES = {
    "account_id": "ANIME_MEMES_R2_ACCOUNT_ID",
    "access_key_id": "ANIME_MEMES_R2_ACCESS_KEY_ID",
    "secret_access_key": "ANIME_MEMES_R2_SECRET_ACCESS_KEY",
    "bucket": "ANIME_MEMES_R2_BUCKET",
    "public_base_url": "ANIME_MEMES_R2_PUBLIC_BASE_URL",
}
PREFIX_ENV = "ANIME_MEMES_R2_PREFIX"
R2_ENV_VARS = (*ENV_NAMES.values(), PREFIX_ENV)
DEFAULT_PREFIX = "memes/"
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9/_.-]{0,511}")


class R2Error(RuntimeError):
    """R2 請求失敗；訊息只含狀態碼與 S3 錯誤代碼。"""


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def signature_v4(*, method: str, host: str, path: str, headers: Mapping[str, str], payload_hash: str,
                 access_key: str, secret_key: str, region: str, service: str, amz_datetime: str,
                 query: str = "") -> str:
    """回傳 Authorization 標頭值。headers 為要簽入的標頭（不含 host，函式自行加入）。

    path 必須是尚未編碼的路徑；每個路徑段依 S3 規則 URI 編碼，保留 "/"。
    """
    signed = {name.lower().strip(): " ".join(str(value).split()) for name, value in headers.items()}
    signed["host"] = host
    names = sorted(signed)
    canonical_headers = "".join(f"{name}:{signed[name]}\n" for name in names)
    signed_headers = ";".join(names)
    canonical_uri = "/".join(quote(segment, safe="-_.~") for segment in path.split("/"))
    canonical_request = "\n".join([method, canonical_uri, query, canonical_headers, signed_headers, payload_hash])
    date = amz_datetime[:8]
    scope = f"{date}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_datetime, scope,
                                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
    key = _hmac(("AWS4" + secret_key).encode("utf-8"), date)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}")


@dataclass(frozen=True)
class R2Config:
    account_id: str
    access_key_id: str
    secret_access_key: str = field(repr=False)
    bucket: str
    public_base_url: str
    prefix: str = DEFAULT_PREFIX

    def __post_init__(self):
        if not re.fullmatch(r"[0-9a-f]{32}", self.account_id):
            raise ValueError(f"{ENV_NAMES['account_id']} 應為 32 字元的十六進位帳號 ID")
        if not self.access_key_id or not self.secret_access_key:
            raise ValueError("R2 存取金鑰不可為空")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", self.bucket):
            raise ValueError(f"{ENV_NAMES['bucket']} 不是有效的 bucket 名稱")
        try:
            original_url(self.public_base_url)
        except ValueError as exc:
            raise ValueError(f"{ENV_NAMES['public_base_url']} 必須是公開的 HTTPS 網址") from exc
        if "?" in self.public_base_url:
            raise ValueError(f"{ENV_NAMES['public_base_url']} 不可包含查詢字串")
        if self.prefix and (not self.prefix.endswith("/") or not KEY_PATTERN.fullmatch(self.prefix) or ".." in self.prefix):
            raise ValueError(f"{PREFIX_ENV} 只能使用英數、-_./ 並以 / 結尾")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "R2Config | None":
        """全部未設定時回傳 None（停用 R2）；只設定一部分時報錯，避免誤以為已啟用。"""
        env = os.environ if env is None else env
        values = {name: (env.get(var) or "").strip() for name, var in ENV_NAMES.items()}
        if not any(values.values()):
            return None
        missing = [ENV_NAMES[name] for name, value in values.items() if not value]
        if missing:
            raise ValueError("R2 設定不完整，缺少：" + "、".join(missing))
        prefix = env.get(PREFIX_ENV)
        return cls(**values, prefix=DEFAULT_PREFIX if prefix is None else prefix.strip())

    @property
    def host(self) -> str:
        return f"{self.account_id}.r2.cloudflarestorage.com"


class R2Store:
    """HEAD／PUT 物件與產生公開網址。urlopen、clock 可注入以便測試。"""

    region = "auto"
    service = "s3"

    def __init__(self, config: R2Config, *, urlopen: Callable = urllib.request.urlopen,
                 clock: Callable[[], datetime] | None = None, timeout: float = 30, retries: int = 2):
        self.config = config
        self._urlopen = urlopen
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._timeout = timeout
        self._retries = retries

    def key_for(self, sha256: str, suffix: str = ".webp") -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("內容雜湊格式錯誤")
        return f"{self.config.prefix}{sha256}{suffix}"

    def public_url(self, key: str) -> str:
        self._check_key(key)
        return self.config.public_base_url.rstrip("/") + "/" + quote(key, safe="/-_.~")

    def exists(self, key: str) -> bool:
        try:
            self._request("HEAD", key)
        except R2Error as exc:
            if getattr(exc, "status", None) == 404:
                return False
            raise
        return True

    def put(self, key: str, data: bytes, *, content_type: str = "image/webp",
            cache_control: str = IMMUTABLE_CACHE) -> None:
        self._request("PUT", key, data, {"content-type": content_type, "cache-control": cache_control})

    def upload_if_missing(self, key: str, data: bytes, **kwargs) -> bool:
        """物件不存在才上傳；回傳是否實際上傳。"""
        if self.exists(key):
            return False
        self.put(key, data, **kwargs)
        return True

    @staticmethod
    def _check_key(key: str) -> None:
        if not KEY_PATTERN.fullmatch(key) or ".." in key or "//" in key:
            raise ValueError("R2 物件鍵格式錯誤")

    def _request(self, method: str, key: str, data: bytes | None = None,
                 extra: Mapping[str, str] | None = None) -> None:
        self._check_key(key)
        payload = data or b""
        path = f"/{self.config.bucket}/{key}"
        url = f"https://{self.config.host}" + "/".join(quote(s, safe="-_.~") for s in path.split("/"))
        for attempt in range(self._retries + 1):
            amz_datetime = self._clock().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            payload_hash = hashlib.sha256(payload).hexdigest() if data is not None else EMPTY_SHA256
            headers = {"x-amz-content-sha256": payload_hash, "x-amz-date": amz_datetime, **(extra or {})}
            headers["Authorization"] = signature_v4(
                method=method, host=self.config.host, path=path, headers=headers, payload_hash=payload_hash,
                access_key=self.config.access_key_id, secret_key=self.config.secret_access_key,
                region=self.region, service=self.service, amz_datetime=amz_datetime)
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with self._urlopen(request, timeout=self._timeout) as response:
                    response.read()
                return
            except urllib.error.HTTPError as exc:
                code = _error_code(exc)
                error = R2Error(f"R2 {method} 失敗：HTTP {exc.code}{'（' + code + '）' if code else ''}")
                error.status = exc.code
                if exc.code < 500 or attempt == self._retries:
                    raise error from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt == self._retries:
                    raise R2Error(f"R2 {method} 連線失敗：{type(exc).__name__}") from None
            time.sleep(0.5 * (attempt + 1))


def _error_code(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read(2048).decode("utf-8", "replace")
    except Exception:
        return ""
    match = re.search(r"<Code>([A-Za-z0-9]{1,64})</Code>", body)
    return match.group(1) if match else ""


def store_from_env(env: Mapping[str, str] | None = None) -> R2Store | None:
    config = R2Config.from_env(env)
    return R2Store(config) if config else None
