from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock

from src.anime_memes.db import connect, init_db
from src.anime_memes.discord_sync import sync_discord, sync_status


class DiscordSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'test.sqlite3'
        with closing(connect(self.db)) as conn:
            init_db(conn)
        self.fixed = self.root / 'fixed.json'

    def write_export(self, messages):
        self.fixed.write_text(json.dumps({'messages': messages}), encoding='utf-8')

    def run_sync(self, **overrides):
        options = dict(
            db_path=self.db, token='test-token', channel_id='test', exe='exporter',
            raw_dir=self.root / 'raw', fixed_dir=self.root / 'fixed', backup_dir=self.root / 'backups',
            before='2026-09-24',
            export_fn=MagicMock(return_value=(self.fixed, self.fixed)),
            download_fn=MagicMock(return_value={'failed': 0}),
            thumbnails_fn=MagicMock(return_value={'failed': 0}),
        )
        options.update(overrides)
        return sync_discord(**options)

    def message(self, message_id, content='hi', **extra):
        return {'id': message_id, 'timestamp': '2026-09-23T12:00:00+08:00', 'content': content,
                'attachments': [], **extra}

    def test_new_messages_are_ingested_and_reported(self):
        self.write_export([self.message('m1'), self.message('m2')])
        report = self.run_sync()
        self.assertEqual(report['state'], 'initial_import')
        self.assertEqual(report['new_message_ids'], ['m1', 'm2'])
        self.assertEqual(report['special_cases']['edited_message_ids'], [])
        self.assertEqual(report['cursor']['covered_through'], '2026-09-24')
        with closing(connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM discord_messages').fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT status FROM export_runs').fetchone()[0], 'success')

    def test_second_sync_reports_edit_and_no_new_ids(self):
        self.write_export([self.message('m1')])
        self.run_sync()
        self.write_export([self.message('m1', content='edited')])
        report = self.run_sync()
        self.assertEqual(report['state'], 'updated')
        self.assertEqual(report['new_message_ids'], [])
        self.assertEqual(report['special_cases']['edited_message_ids'], ['m1'])

    def test_media_failure_reports_partial_without_cursor(self):
        self.write_export([self.message('m1')])
        report = self.run_sync(download_fn=MagicMock(return_value={'failed': 1}))
        self.assertEqual(report['state'], 'partial')
        self.assertIsNone(report['cursor'])
        with closing(connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT status FROM export_runs').fetchone()[0], 'partial')

    def test_skip_flags_do_not_call_media_steps(self):
        self.write_export([self.message('m1')])
        download, thumbs = MagicMock(), MagicMock()
        report = self.run_sync(download_fn=download, thumbnails_fn=thumbs,
                               skip_attachments=True, skip_thumbnails=True)
        download.assert_not_called()
        thumbs.assert_not_called()
        self.assertIsNone(report['attachments'])
        self.assertIsNone(report['thumbnails'])

    def test_sync_status_before_and_after_sync(self):
        before = sync_status(db_path=self.db, channel_id='test')
        self.assertFalse(before['initialized'])
        self.assertEqual(before['channel_id'], 'test')
        self.write_export([self.message('m1')])
        self.run_sync()
        after = sync_status(db_path=self.db, channel_id='test')
        self.assertTrue(after['initialized'])
        self.assertEqual(after['covered_through'], '2026-09-24')
        self.assertEqual(after['last_message_id'], 'm1')


if __name__ == '__main__':
    unittest.main()
