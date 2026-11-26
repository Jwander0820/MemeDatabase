from __future__ import annotations

import copy
import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from src.anime_memes.attachments import download_attachment
from src.anime_memes.cli import build_parser, cmd_sync_discord
from src.anime_memes.db import backup_database, connect, init_db
from src.anime_memes.exporter import incremental_after, run_discord_export
from src.anime_memes.images import download_all_attachments
from src.anime_memes.tagging import decide_tag_suggestion, export_tagging_jobs, import_tagging_results
from src.anime_memes.tags import add_entry_tag, import_llm_suggestions, load_rules, load_taxonomy, sync_taxonomy, validate_taxonomy
from src.anime_memes.web import create_app


class MaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'test.sqlite3'
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        for number in (1, 2):
            image = self.root / f'{number}.png'
            Image.new('RGB', (8, 8)).save(image)
            self.conn.execute("INSERT INTO discord_messages(id,timestamp) VALUES(?, '2026-07-13')", (f'm{number}',))
            self.conn.execute("INSERT INTO attachments(id,message_id,file_name,local_path) VALUES(?,?,?,?)",
                              (f'a{number}', f'm{number}', image.name, str(image)))
            self.conn.execute("""INSERT INTO meme_entries(key,attachment_id,source_message_id,quote_text,pairing_method,confidence,status)
                                 VALUES(?,?,?,'','test',1,'ready')""", (f'entry{number}', f'a{number}', f'm{number}'))
        self.conn.commit()

    def export(self):
        return export_tagging_jobs(self.conn, self.root / 'jobs.jsonl', actor_name='test')['run_id']

    def result(self, run, key='entry1', tags=None):
        return {'schema_version': 1, 'run_id': run, 'key': key, 'image_summary': 'test image',
                'suggestions': tags if tags is not None else [{'tag_id': 'reaction_roast', 'confidence': .9, 'reason': 'test reason'}]}

    def import_results(self, *items):
        path = self.root / 'results.jsonl'
        path.write_text('\n'.join(json.dumps(item) for item in items), encoding='utf-8')
        return import_tagging_results(self.conn, path)

    def test_repeated_init_does_not_rewrite_schema_or_tags(self):
        version = self.conn.execute('PRAGMA schema_version').fetchone()[0]
        changes = self.conn.total_changes
        init_db(self.conn)
        self.assertEqual(version, self.conn.execute('PRAGMA schema_version').fetchone()[0])
        self.assertEqual(changes, self.conn.total_changes)

    def test_web_reads_do_not_initialize_database_per_request(self):
        app = create_app(db_path=self.db)
        with patch('src.anime_memes.web.init_db', side_effect=AssertionError('request tried migration')):
            self.assertEqual(app.test_client().get('/api/entries').status_code, 200)
            self.assertEqual(app.test_client().get('/api/entries/entry1').status_code, 200)

    def test_taxonomy_rename_preserves_links_and_suggestions(self):
        run = self.export()
        self.import_results(self.result(run))
        suggestion = self.conn.execute('SELECT id FROM tag_suggestions').fetchone()[0]
        decide_tag_suggestion(self.conn, suggestion, decision='accepted', reviewed_by='test')
        taxonomy = copy.deepcopy(load_taxonomy())
        taxonomy['tags'][0]['label'] = '新的顯示名稱'
        sync_taxonomy(self.conn, taxonomy)
        self.conn.commit()
        self.assertEqual(self.conn.execute('SELECT tag_name,tag_id FROM entry_tags').fetchone()[:],
                         ('新的顯示名稱', 'reaction_roast'))
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_alias_migration_maps_formal_link_to_canonical_tag(self):
        self.conn.execute("INSERT INTO tags(name,source) VALUES('毒舌','manual')")
        self.conn.execute("INSERT INTO entry_tags(entry_key,tag_name,source) VALUES('entry1','毒舌','manual')")
        self.conn.execute("INSERT INTO tags(name,source) VALUES('未知舊標籤','auto')")
        self.conn.execute("INSERT INTO tags(name,source,id) VALUES('已遷移舊標籤','manual','legacy_preexisting')")
        init_db(self.conn)
        self.assertEqual(self.conn.execute('SELECT tag_name,tag_id FROM entry_tags').fetchone()[:],
                         ('嗆人', 'reaction_roast'))
        self.assertEqual(self.conn.execute("SELECT enabled FROM tags WHERE name='未知舊標籤'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT enabled FROM tags WHERE name='已遷移舊標籤'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_export_skips_corrupt_image(self):
        (self.root / '1.png').write_bytes(b'not an image')
        result = export_tagging_jobs(self.conn, self.root / 'jobs.jsonl', actor_name='test')
        self.assertEqual(result['jobs'], 1)
        self.assertEqual(result['skipped_invalid'], 1)

    def test_review_does_not_complete_partially_imported_run(self):
        run = self.export()
        self.import_results(self.result(run))
        suggestion = self.conn.execute('SELECT id FROM tag_suggestions').fetchone()[0]
        decide_tag_suggestion(self.conn, suggestion, decision='rejected', reviewed_by='test')
        self.assertNotEqual(self.conn.execute('SELECT status FROM tagging_runs').fetchone()[0], 'completed')
        self.import_results(self.result(run, 'entry2', []))
        self.assertEqual(self.conn.execute('SELECT status FROM tagging_runs').fetchone()[0], 'completed')

    def test_cannot_accept_tag_disabled_after_import(self):
        run = self.export()
        self.import_results(self.result(run))
        suggestion = self.conn.execute('SELECT id FROM tag_suggestions').fetchone()[0]
        self.conn.execute("UPDATE tags SET enabled=0 WHERE id='reaction_roast'")
        self.conn.commit()
        with self.assertRaises(ValueError):
            decide_tag_suggestion(self.conn, suggestion, decision='accepted', reviewed_by='test')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM entry_tags').fetchone()[0], 0)

    def test_legacy_invalid_line_is_atomic_and_confidence_is_validated(self):
        path = self.root / 'legacy.jsonl'
        items = [
            {'key':'entry1','suggested_tags':['嗆人','unknown'],'confidence':.9,'reason':'test'},
            {'key':'entry2','suggested_tags':['嗆人'],'confidence':float('inf'),'reason':'test'},
        ]
        path.write_text('\n'.join(json.dumps(item) for item in items), encoding='utf-8')
        self.assertEqual(import_llm_suggestions(self.conn, path), (0, 2))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM tag_suggestions').fetchone()[0], 0)

    def test_exporter_failure_does_not_disclose_token(self):
        secret = 'TEST_SECRET_DO_NOT_PRINT'
        error = subprocess.CalledProcessError(1, ['exporter', '-t', secret])
        with patch('src.anime_memes.exporter.subprocess.run', side_effect=error):
            with self.assertRaises(Exception) as caught:
                run_discord_export(exporter_exe='exporter', token=secret, channel_id='test', output_path=self.root / 'raw.json')
        self.assertNotIn(secret, str(caught.exception))

    def sync_args(self):
        return build_parser().parse_args([
            '--db', str(self.db), 'sync-discord', '--channel-id', 'test', '--token', 'test-token',
            '--exe', 'test-exporter', '--before', '2026-09-24', '--backup-dir', str(self.root / 'backups'),
        ])

    def test_sync_media_failure_preserves_retry_range_then_recovers(self):
        args = self.sync_args()
        original_after = incremental_after(self.conn, 'test')
        fixed = self.root / 'fixed.json'
        fixed.write_text(json.dumps({'messages': [
            {'id': 'new-message', 'timestamp': '2026-09-23T12:00:00+08:00', 'content': 'test', 'attachments': []}
        ]}), encoding='utf-8')
        with patch('src.anime_memes.cli.export_and_convert', return_value=(fixed, fixed)), \
             patch('src.anime_memes.cli.download_all_attachments', return_value={'failed': 1}) as download, \
             patch('src.anime_memes.cli.build_thumbnails', return_value={'failed': 0}), redirect_stdout(StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cmd_sync_discord(args)
            self.assertEqual(caught.exception.code, 2)
            self.assertTrue(download.call_args.kwargs['retry_failed'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM discord_sync_state').fetchone()[0], 0)
        self.assertEqual(incremental_after(self.conn, 'test'), original_after)
        self.assertEqual(self.conn.execute('SELECT status FROM export_runs ORDER BY id DESC').fetchone()[0], 'partial')
        with patch('src.anime_memes.cli.export_and_convert', return_value=(fixed, fixed)), \
             patch('src.anime_memes.cli.download_all_attachments', return_value={'failed': 0}), \
             patch('src.anime_memes.cli.build_thumbnails', return_value={'failed': 0}), redirect_stdout(StringIO()):
            cmd_sync_discord(args)
        self.assertEqual(self.conn.execute('SELECT covered_through FROM discord_sync_state').fetchone()[0], '2026-09-24')
        self.assertEqual(incremental_after(self.conn, 'test'), '2026-09-23')

    def test_sync_backs_up_before_initialization(self):
        def fail_initialization(_conn):
            self.assertEqual(len(list((self.root / 'backups').glob('*.sqlite3'))), 1)
            raise ValueError('test migration failure')
        with patch('src.anime_memes.cli.init_db', side_effect=fail_initialization):
            with self.assertRaisesRegex(ValueError, 'test migration failure'):
                cmd_sync_discord(self.sync_args())

    def test_backup_contains_committed_wal_data(self):
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute("UPDATE meme_entries SET notes='committed in WAL' WHERE key='entry1'")
        self.conn.commit()
        backup = backup_database(self.db, self.root / 'backups')
        with closing(sqlite3.connect(backup)) as saved:
            self.assertEqual(saved.execute("SELECT notes FROM meme_entries WHERE key='entry1'").fetchone()[0], 'committed in WAL')

    def test_interrupted_download_is_not_cached_on_retry(self):
        output = self.root / 'download.png'
        response = BytesIO(b'partial')
        response.headers = {'Content-Length': '100'}
        with patch('src.anime_memes.attachments.urllib.request.urlopen', return_value=response):
            status, _error = download_attachment('https://example.test/image', output)
        self.assertEqual(status, 'failed')
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob('*.part')), [])
        response = BytesIO(b'complete')
        response.headers = {'Content-Length': '8'}
        with patch('src.anime_memes.attachments.urllib.request.urlopen', return_value=response):
            status, _error = download_attachment('https://example.test/image', output)
        self.assertEqual(status, 'downloaded')
        self.assertEqual(output.read_bytes(), b'complete')

    def test_swapping_taxonomy_labels_preserves_stable_ids(self):
        add_entry_tag(self.conn, 'entry1', '嗆人', 'manual')
        add_entry_tag(self.conn, 'entry2', '無言', 'manual')
        self.conn.commit()
        taxonomy = copy.deepcopy(load_taxonomy())
        taxonomy['tags'][0]['label'], taxonomy['tags'][1]['label'] = taxonomy['tags'][1]['label'], taxonomy['tags'][0]['label']
        sync_taxonomy(self.conn, taxonomy)
        self.conn.commit()
        rows = self.conn.execute('SELECT entry_key,tag_name,tag_id FROM entry_tags ORDER BY entry_key').fetchall()
        self.assertEqual([tuple(row) for row in rows], [('entry1','無言','reaction_roast'),('entry2','嗆人','reaction_speechless')])
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_alias_and_canonical_duplicate_merge_without_losing_other_sources(self):
        add_entry_tag(self.conn, 'entry1', '嗆人', 'manual', reason='canonical')
        add_entry_tag(self.conn, 'entry1', '毒舌', 'manual')
        add_entry_tag(self.conn, 'entry1', '毒舌', 'auto')
        init_db(self.conn)
        rows = self.conn.execute('SELECT tag_name,source,tag_id FROM entry_tags ORDER BY source').fetchall()
        self.assertEqual([tuple(row) for row in rows], [('嗆人','auto','reaction_roast'),('嗆人','manual','reaction_roast')])
        self.assertEqual(self.conn.execute("SELECT reason FROM entry_tags WHERE source='manual'").fetchone()[0], 'canonical')

    def test_tag_api_replaces_all_sources_and_keeps_retained_source(self):
        run = self.export()
        self.import_results(self.result(run))
        suggestion = self.conn.execute('SELECT id FROM tag_suggestions').fetchone()[0]
        decide_tag_suggestion(self.conn, suggestion, decision='accepted', reviewed_by='test')
        add_entry_tag(self.conn, 'entry1', '工作', 'auto')
        add_entry_tag(self.conn, 'entry1', '人工測試', 'manual')
        self.conn.commit()
        client = create_app(db_path=self.db).test_client()
        self.assertEqual(set(client.get('/api/entries/entry1').get_json()['tags']), {'嗆人', '工作', '人工測試'})
        response = client.put('/api/entries/entry1/tags', json={'tags': '嗆人, 新人工'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.get_json()['tags']), {'嗆人', '新人工'})
        rows = dict(self.conn.execute("SELECT tag_name, source FROM entry_tags WHERE entry_key='entry1'").fetchall())
        self.assertEqual(rows, {'嗆人': 'suggestion', '新人工': 'manual'})


    def test_concurrent_opposite_review_cannot_create_accepted_tag(self):
        from unittest.mock import Mock
        run = self.export()
        self.import_results(self.result(run))
        suggestion = self.conn.execute('SELECT id FROM tag_suggestions').fetchone()[0]
        raced = False
        def execute(sql, params=()):
            nonlocal raced
            if 'UPDATE tag_suggestions SET status=' in sql and not raced:
                raced = True
                with closing(connect(self.db)) as other:
                    decide_tag_suggestion(other, suggestion, decision='rejected', reviewed_by='other')
            return self.conn.execute(sql, params)
        wrapped = Mock(wraps=self.conn)
        wrapped.execute.side_effect = execute
        with self.assertRaises(RuntimeError):
            decide_tag_suggestion(wrapped, suggestion, decision='accepted', reviewed_by='test')
        self.assertEqual(self.conn.execute('SELECT status FROM tag_suggestions').fetchone()[0], 'rejected')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM entry_tags').fetchone()[0], 0)

    def test_taxonomy_validation_rejects_ambiguous_alias_and_non_ascii_id(self):
        for change in ('alias', 'id', 'limit'):
            taxonomy = copy.deepcopy(load_taxonomy())
            if change == 'alias':
                taxonomy['tags'][0]['aliases'].append(taxonomy['tags'][1]['label'])
            elif change == 'id':
                taxonomy['tags'][0]['id'] = 'é_invalid'
            else:
                taxonomy['policy']['max_tags_per_entry'] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_taxonomy(taxonomy)
        with self.assertRaises(ValueError):
            load_rules(self.root / 'missing.json')

    def test_force_download_replaces_cache_and_skips_manual_uploads(self):
        cached = self.root / 'a1.png'
        cached.write_bytes(b'old broken download')
        self.conn.execute("UPDATE attachments SET discord_url='https://example.test/image' WHERE id='a1'")
        self.conn.execute("UPDATE discord_messages SET type='Manual' WHERE id='m2'")
        self.conn.commit()
        response = BytesIO((self.root / '1.png').read_bytes())
        response.headers = {}
        with patch('src.anime_memes.attachments.urllib.request.urlopen', return_value=response):
            summary = download_all_attachments(self.conn, attachment_dir=self.root, force=True)
        self.assertEqual(summary['candidates'], 1)
        self.assertEqual(summary['downloaded'], 1)
        self.assertEqual(cached.read_bytes(), (self.root / '1.png').read_bytes())
        response = BytesIO(b'truncated')
        response.headers = {'Content-Length': '999'}
        with patch('src.anime_memes.attachments.urllib.request.urlopen', return_value=response):
            status, _error = download_attachment('https://example.test/image', cached, force=True)
        self.assertEqual(status, 'failed')
        self.assertEqual(cached.read_bytes(), (self.root / '1.png').read_bytes())


if __name__ == '__main__':
    unittest.main()


class ListTagsCommandTests(unittest.TestCase):
    def run_cli(self, *argv):
        # 不經過 main()：main 會載入 .env 並污染其他測試的環境變數。
        args = build_parser().parse_args(list(argv))
        out = StringIO()
        with redirect_stdout(out):
            args.func(args)
        return out.getvalue()

    def test_list_tags_is_read_only_and_filters_free_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite3"
            with closing(connect(db)) as conn:
                init_db(conn)
                conn.execute("PRAGMA foreign_keys=OFF")  # 只測清單輸出，不建完整條目
                for key in ("a", "b"):
                    add_entry_tag(conn, key, "自由標籤", "manual")
                add_entry_tag(conn, "a", "冷門", "manual")
                conn.commit()
            before = db.read_bytes()
            data = json.loads(self.run_cli("--db", str(db), "list-tags", "--format", "json", "--min-count", "2"))
            names = {tag["name"]: tag for tag in data["tags"]}
            self.assertIn("嗆人", names)
            self.assertTrue(names["嗆人"]["in_taxonomy"])
            self.assertEqual(names["自由標籤"]["count"], 2)
            self.assertNotIn("冷門", names)
            self.assertNotIn("多人對話", names)  # 停用的 taxonomy 標籤不列出
            self.assertEqual(db.read_bytes(), before)
            text = self.run_cli("--db", str(Path(tmp) / "missing.sqlite3"), "list-tags")
            self.assertIn("嗆人", text)
            self.assertFalse((Path(tmp) / "missing.sqlite3").exists())
