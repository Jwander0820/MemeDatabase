"""公開 Nginx 的關鍵字搜尋限制：只計有 q 的請求、全站同時上限，且映像內有預設值。"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NginxSearchLimitTests(unittest.TestCase):
    def setUp(self):
        self.conf = (ROOT / "deploy/nginx-public.conf.template").read_text(encoding="utf-8")

    def test_keyword_keys_are_empty_without_query(self):
        # 鍵為空字串時 Nginx 不計數：沒有 q 的瀏覽、詳情、標籤清單不受搜尋限制影響。
        for name in ("keyword_key", "keyword_all_key"):
            block = re.search(r"map \$arg_q \$" + name + r" \{(.*?)\}", self.conf, re.S)
            self.assertIsNotNone(block, name)
            self.assertRegex(block.group(1), r'""\s+"";')
        self.assertIn("limit_req_zone $keyword_key zone=keyword:8m rate=${SEARCH_KEYWORD_RATE};", self.conf)
        self.assertIn("limit_conn_zone $keyword_all_key zone=keyword_all:1m;", self.conf)

    def test_api_location_applies_keyword_limits(self):
        api = self.conf.split("location /api/v1/ {", 1)[1].split("location ", 1)[0]
        self.assertIn("limit_req zone=search burst=${SEARCH_BURST} nodelay;", api)
        self.assertIn("limit_req zone=keyword burst=${SEARCH_KEYWORD_BURST} nodelay;", api)
        self.assertIn("limit_conn keyword_all ${SEARCH_KEYWORD_CONCURRENCY};", api)
        self.assertIn("limit_req_status 429;", api)
        self.assertIn("limit_conn_status 429;", api)

    def test_every_template_variable_has_an_image_default_or_compose_value(self):
        # VPS 的 compose.yaml 是手動下載的，可能比映像舊；未定義的變數不會被 envsubst 取代，Nginx 會無法啟動。
        dockerfile = (ROOT / "deploy/Dockerfile.nginx").read_text(encoding="utf-8")
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        for var in set(re.findall(r"\$\{(SEARCH_[A-Z_]+)\}", self.conf)):
            if var.startswith("SEARCH_KEYWORD_"):
                self.assertRegex(dockerfile, r"ENV .*\b" + var + r"=", var)
            self.assertIn(f"{var}: ${{{var}:-", compose, var)


if __name__ == "__main__":
    unittest.main()
