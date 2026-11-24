"""外部媒體網址的純語法／明確私有位址驗證，不發網路請求。"""
import ipaddress
from urllib.parse import urlsplit

def original_url(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("原圖網址格式錯誤")
    parsed = urlsplit(value)
    host = parsed.hostname
    if parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("原圖必須是無帳密、無 fragment 的 HTTPS 網址")
    try:
        parsed.port
        host.encode("idna")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("原圖網址格式錯誤") from exc
    host = host.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
        raise ValueError("原圖不可使用本機或私有位址")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # 非標準數字位址也不當作公開 DNS 名稱接受。
        if all(c in "0123456789." for c in host) or host.startswith("0x"):
            raise ValueError("原圖位址格式不明確")
    else:
        if not ip.is_global:
            raise ValueError("原圖不可使用本機或私有位址")
    return value
