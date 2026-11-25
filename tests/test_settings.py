import os
import unittest
from unittest.mock import patch

from src.anime_memes import settings


class SettingsTests(unittest.TestCase):
    def test_defaults_without_env(self):
        names = ["MEMES_DB", "MEMES_ATTACHMENT_DIR", "MEMES_THUMBNAIL_DIR",
                 "MEMES_WEB_DIR", "MEMES_RELEASE_ROOT", "MEMES_TAG_RULES"]
        with patch.dict(os.environ, {}, clear=False):
            for name in names:
                os.environ.pop(name, None)
            self.assertEqual(settings.db_path(), settings.DEFAULT_DB)
            self.assertEqual(settings.attachment_dir(), settings.DEFAULT_ATTACHMENT_DIR)
            self.assertEqual(settings.thumbnail_dir(), settings.DEFAULT_THUMBNAIL_DIR)
            self.assertEqual(settings.web_dir(), settings.DEFAULT_WEB_DIR)
            self.assertEqual(settings.release_root(), settings.DEFAULT_RELEASE_ROOT)
            self.assertEqual(settings.tag_rules(), settings.DEFAULT_TAG_RULES)

    def test_env_read_at_call_time(self):
        with patch.dict(os.environ, {"MEMES_DB": "x.db", "MEMES_RELEASE_ROOT": "rel"}):
            self.assertEqual(settings.db_path(), "x.db")
            self.assertEqual(settings.release_root(), "rel")

    def test_empty_web_dir_falls_back(self):
        with patch.dict(os.environ, {"MEMES_WEB_DIR": ""}):
            self.assertEqual(settings.web_dir(), settings.DEFAULT_WEB_DIR)


if __name__ == "__main__":
    unittest.main()
