from contextlib import closing
import os
import unittest
from unittest.mock import patch

from PIL import Image
import test_publication_media as fixtures
from src.anime_memes import publishing as p
from src.anime_memes.public_data import open_readonly
from src.anime_memes.publication_cache import PublicationCache


class PublicationCacheTest(unittest.TestCase):
    setUp = fixtures.PublicationMediaTest.setUp
    add = fixtures.PublicationMediaTest.add
    public_rows = fixtures.PublicationMediaTest.public_rows

    def select(self):
        p.update_selection(self.conn, 'meme_0', {'publish_selected': True})

    def test_check_reuses_and_full_decodes(self):
        self.select()
        self.assertEqual(p.check_database(self.db)['media_validation']['decoded'], 1)
        self.assertEqual(p.check_database(self.db)['media_validation']['decoded'], 0)
        self.assertEqual(p.check_database(self.db, force=True)['media_validation']['decoded'], 1)

    def test_metadata_only_reuses_thumbnail_and_updates_snapshot(self):
        self.select()
        first = p.export_release(self.db, self.releases, store=None)
        self.conn.execute("UPDATE meme_entries SET quote_text='new caption' WHERE key='meme_0'")
        self.conn.commit()
        with patch.object(p, 'render_webp', side_effect=AssertionError('must reuse')):
            second = p.export_release(self.db, self.releases, store=None)
        self.assertEqual(second['report']['media_validation']['decoded'], 0)
        self.assertEqual(second['report']['media_validation']['thumbnails_reused'], 1)
        self.assertEqual(self.public_rows(first['version'])[0][1], self.public_rows(second['version'])[0][1])
        with closing(open_readonly(self.releases/'versions'/second['version']/'public.sqlite3')) as c:
            self.assertEqual(c.execute('SELECT quote_text FROM entries').fetchone()[0], 'new caption')

    def test_replacement_with_preserved_size_and_mtime_invalidates(self):
        self.select()
        Image.new('RGB', (80, 60), 'blue').save(self.original, format='BMP')
        first = p.export_release(self.db, self.releases, store=None)
        old = self.original.stat()
        Image.new('RGB', (80, 60), 'red').save(self.original, format='BMP')
        os.utime(self.original, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.assertEqual(self.original.stat().st_size, old.st_size)
        second = p.export_release(self.db, self.releases, store=None)
        self.assertEqual(second['report']['media_validation']['decoded'], 1)
        self.assertNotEqual(self.public_rows(first['version'])[0][1], self.public_rows(second['version'])[0][1])

    def test_removed_derivative_regenerates_and_corrupt_derivative_fails(self):
        self.select()
        first = p.export_release(self.db, self.releases, store=None)
        target = self.releases/'media'/self.public_rows(first['version'])[0][1].split('/')[-1]
        target.unlink()
        second = p.export_release(self.db, self.releases, store=None)
        self.assertEqual(second['report']['media_validation']['thumbnails_rendered'], 1)
        target.write_bytes(b'bad')
        with self.assertRaises(ValueError):p.export_release(self.db, self.releases, store=None)

    def test_corrupt_cache_is_disposable(self):
        self.select()
        (self.db.parent/'.publication-cache.sqlite3').write_bytes(b'bad')
        self.assertTrue(p.check_database(self.db)['valid'])

    def test_changed_source_updates_r2_and_720p(self):
        self.select()
        store = fixtures.FakeStore()
        first = p.export_release(self.db, self.releases, store=store)
        Image.new('RGB', (1920, 1080), 'red').save(self.original)
        second = p.export_release(self.db, self.releases, store=store)
        self.assertEqual(len(store.uploads), 2)
        self.assertNotEqual(self.public_rows(first['version'])[0][0], self.public_rows(second['version'])[0][0])

    def test_unchanged_source_reuses_720p_and_r2(self):
        self.select()
        store = fixtures.FakeStore()
        p.export_release(self.db, self.releases, store=store)
        with patch.object(p, 'ensure_web_rendition', wraps=p.ensure_web_rendition) as ensure:
            second = p.export_release(self.db, self.releases, store=store)
        self.assertEqual(len(store.uploads), 1)
        self.assertFalse(ensure.call_args.kwargs['force'])
        self.assertEqual(second['media']['reused'], 1)

    def test_validation_rules_invalidate(self):
        self.select()
        p.check_database(self.db)
        with patch('src.anime_memes.publication_cache.PILLOW_VERSION', 'changed'):
            self.assertEqual(p.check_database(self.db)['media_validation']['decoded'], 1)

    def test_changed_fields_still_block_publication(self):
        self.select()
        p.check_database(self.db)
        self.conn.execute("UPDATE meme_entries SET status='needs_review' WHERE key='meme_0'")
        self.conn.commit()
        report = p.check_database(self.db)
        self.assertFalse(report['valid'])
        self.assertEqual(report['media_validation']['decoded'], 0)
