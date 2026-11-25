import unittest

from src.anime_memes.urls import original_url


class OriginalUrlTests(unittest.TestCase):
    def test_合法_https_原樣回傳(self):
        for url in ("https://example.com/a.png", "https://cdn.example.co.jp/x/y.jpg?v=1", "https://8.8.8.8/a.png"):
            self.assertEqual(original_url(url), url)

    def test_空值與_None_回傳_None(self):
        self.assertIsNone(original_url(None))
        self.assertIsNone(original_url(""))

    def test_http_被拒(self):
        with self.assertRaises(ValueError):
            original_url("http://example.com/a.png")

    def test_帳密被拒(self):
        for url in ("https://user:pw@example.com/a.png", "https://user@example.com/a.png", "https://:pw@example.com/a.png"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                original_url(url)

    def test_fragment_被拒(self):
        with self.assertRaises(ValueError):
            original_url("https://example.com/a.png#frag")

    def test_本機名稱被拒(self):
        for url in ("https://localhost/a.png", "https://LOCALHOST./a.png", "https://app.localhost/a.png",
                    "https://printer.local/a.png", "https://db.internal/a.png", "https://intranet/a.png"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                original_url(url)

    def test_非公開_IP_被拒(self):
        for url in ("https://127.0.0.1/a.png", "https://[::1]/a.png", "https://10.0.0.5/a.png",
                    "https://192.168.1.1/a.png", "https://172.16.0.1/a.png", "https://169.254.169.254/latest",
                    "https://0.0.0.0/a.png", "https://[fe80::1]/a.png", "https://[fc00::1]/a.png"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                original_url(url)

    def test_非標準數字位址被拒(self):
        for url in ("https://127.1/a.png", "https://0x7f.0.0.1/a.png", "https://2130706433.1/a.png"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                original_url(url)

    def test_非法字串被拒(self):
        for value in ("not a url", "example.com/a.png", "https://", "https:///a.png", "ftp://example.com/a.png",
                      "https://exa mple.com/a.png", "https://example.com/a\n.png", "https://example.com:99999/a.png",
                      "https://example.com:abc/a.png", "https://" + "a" * 2050 + ".com/", 123, ["https://example.com"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                original_url(value)


if __name__ == "__main__":
    unittest.main()
