from __future__ import annotations

import os
import unittest
from pathlib import Path

from src.anime_memes.ocr import run_ocr


@unittest.skipUnless(
    os.environ.get("RUN_PADDLEOCR_GPU_TEST") == "1",
    "set RUN_PADDLEOCR_GPU_TEST=1 to run the real PaddleOCR GPU smoke test",
)
class PaddleOcrGpuSmokeTest(unittest.TestCase):
    def test_paddleocr_gpu_smoke(self) -> None:
        image_path = Path(__file__).with_name("test.png")
        text, status = run_ocr(image_path, engine="paddle", device="gpu")

        self.assertEqual(status, "ok:paddleocr:gpu")
        self.assertTrue(text.strip())
