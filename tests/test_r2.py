import hashlib
import io
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest.mock import patch

from src.anime_memes.r2 import EMPTY_SHA256, R2Config, R2Error, R2Store, signature_v4

ACCOUNT = "0123456789abcdef0123456789abcdef"
SECRET = "TOP-SECRET-VALUE"


def config(**overrides):
    values = dict(account_id=ACCOUNT, access_key_id="AKID", secret_access_key=SECRET,
                  bucket="memes-public", public_base_url="https://img.example.com")
    values.update(overrides)
    return R2Config(**values)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(b"")


def http_error(code, body=b""):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body))


class SignatureTest(unittest.TestCase):
    def test_matches_aws_documented_get_object_example(self):
        # AWS S3 SigV4 文件「GET Object」範例的已知簽章。
        auth = signature_v4(
            method="GET", host="examplebucket.s3.amazonaws.com", path="/test.txt",
            headers={"range": "bytes=0-9", "x-amz-content-sha256": EMPTY_SHA256, "x-amz-date": "20130524T000000Z"},
            payload_hash=EMPTY_SHA256, access_key="AKIAIOSFODNN7EXAMPLE",
            secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", region="us-east-1", service="s3",
            amz_datetime="20130524T000000Z")
        self.assertEqual(auth, "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
                               "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
                               "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


class ConfigTest(unittest.TestCase):
    def test_from_env_disabled_partial_and_full(self):
        self.assertIsNone(R2Config.from_env({}))
        with self.assertRaisesRegex(ValueError, "MEMES_R2_BUCKET"):
            R2Config.from_env({"MEMES_R2_ACCOUNT_ID": ACCOUNT})
        env = {"MEMES_R2_ACCOUNT_ID": ACCOUNT, "MEMES_R2_ACCESS_KEY_ID": "AKID",
               "MEMES_R2_SECRET_ACCESS_KEY": SECRET, "MEMES_R2_BUCKET": "memes-public",
               "MEMES_R2_PUBLIC_BASE_URL": "https://img.example.com"}
        cfg = R2Config.from_env(env)
        self.assertEqual(cfg.prefix, "memes/")
        self.assertNotIn(SECRET, repr(cfg))

    def test_rejects_unsafe_values(self):
        for overrides in ({"account_id": "abc"}, {"bucket": "Bad_Bucket"},
                          {"public_base_url": "http://img.example.com"}, {"public_base_url": "https://localhost"},
                          {"prefix": "../x/"}, {"prefix": "memes"}):
            with self.subTest(overrides), self.assertRaises(ValueError):
                config(**overrides)


class StoreTest(unittest.TestCase):
    def store(self, opener):
        clock = lambda: datetime(2026, 11, 24, 1, 2, 3, tzinfo=timezone.utc)
        return R2Store(config(), urlopen=opener, clock=clock)

    def test_key_and_public_url(self):
        store = self.store(FakeOpener())
        sha = hashlib.sha256(b"x").hexdigest()
        key = store.key_for(sha)
        self.assertEqual(key, f"memes/{sha}.webp")
        self.assertEqual(store.public_url(key), f"https://img.example.com/memes/{sha}.webp")
        for bad in ("../a.webp", "/a.webp", "a//b.webp", "a b.webp"):
            with self.subTest(bad), self.assertRaises(ValueError):
                store.public_url(bad)
        with self.assertRaises(ValueError):
            store.key_for("not-a-hash")

    def test_upload_if_missing_heads_then_puts_signed_request(self):
        opener = FakeOpener(http_error(404), None)
        store = self.store(opener)
        self.assertTrue(store.upload_if_missing("memes/a.webp", b"DATA"))
        head, put = opener.requests
        self.assertEqual(head.get_method(), "HEAD")
        self.assertEqual(put.get_method(), "PUT")
        self.assertEqual(put.full_url, f"https://{ACCOUNT}.r2.cloudflarestorage.com/memes-public/memes/a.webp")
        headers = {k.lower(): v for k, v in put.header_items()}
        self.assertEqual(headers["x-amz-content-sha256"], hashlib.sha256(b"DATA").hexdigest())
        self.assertEqual(headers["x-amz-date"], "20261124T010203Z")
        self.assertEqual(headers["content-type"], "image/webp")
        self.assertIn("immutable", headers["cache-control"])
        self.assertIn("SignedHeaders=cache-control;content-type;host;x-amz-content-sha256;x-amz-date",
                      headers["authorization"])
        self.assertEqual(put.data, b"DATA")

    def test_delete_is_signed_idempotent_and_limited_to_prefix(self):
        opener = FakeOpener(None, http_error(404))
        store = self.store(opener)
        store.delete("memes/a.webp")
        store.delete("memes/gone.webp")  # 已不存在也視為成功
        request = opener.requests[0]
        self.assertEqual(request.get_method(), "DELETE")
        self.assertIn("Signature=", dict(request.header_items())["Authorization"])
        self.assertTrue(store.owns("memes/a.webp"))
        self.assertFalse(store.owns("other/a.webp"))
        with self.assertRaises(ValueError):
            store.delete("other/a.webp")
        self.assertEqual(len(opener.requests), 2, "前綴以外的物件不可送出刪除請求")

    def test_existing_object_is_not_uploaded(self):
        opener = FakeOpener(None)
        self.assertFalse(self.store(opener).upload_if_missing("memes/a.webp", b"DATA"))
        self.assertEqual(len(opener.requests), 1)

    @patch("src.anime_memes.r2.time.sleep", lambda _s: None)
    def test_retries_server_errors_and_never_leaks_secret(self):
        opener = FakeOpener(http_error(503), None)
        self.store(opener).put("memes/a.webp", b"DATA")
        self.assertEqual(len(opener.requests), 2)
        opener = FakeOpener(http_error(403, b"<Error><Code>AccessDenied</Code></Error>"))
        with self.assertRaises(R2Error) as caught:
            self.store(opener).put("memes/a.webp", b"DATA")
        self.assertIn("AccessDenied", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
