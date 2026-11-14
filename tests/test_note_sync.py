import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import note_tool as nt
import note_sync


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bdir = self.root / 'brands/test'
        self.bdir.mkdir(parents=True)
        (self.bdir / 'config.json').write_text(json.dumps({'brand': 'test', 'rss': 'https://note.com/test/rss'}))
        self.root_patch = patch.object(nt, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.args = argparse.Namespace(brand='test', delay=5, limit=0, refresh=False)
        self.item = {'title': 'test', 'url': 'https://note.com/test/n/n123', 'date': '2026-11-14'}

    def fetch(self, items, page=''):
        with patch.object(nt, 'Polite') as polite, patch.object(nt, 'read_rss', return_value=items):
            polite.return_value.get.return_value = page
            result = nt.cmd_fetch(self.args)
            return result, polite.return_value.get.call_count

    def test_new_then_no_change_bytes(self):
        result, calls = self.fetch([self.item], '<div class="note-common-styles__textnote-body"><p>Hello</p></div>')
        self.assertEqual((result['saved'], calls), (1, 1))
        before = {p.relative_to(self.bdir): p.read_bytes() for p in self.bdir.rglob('*') if p.is_file()}
        result, calls = self.fetch([self.item])
        self.assertEqual((result['targets'], calls), (0, 0))
        self.assertEqual(before, {p.relative_to(self.bdir): p.read_bytes() for p in self.bdir.rglob('*') if p.is_file()})

    def test_failure_and_recovery(self):
        result, _ = self.fetch([self.item], '<html></html>')
        self.assertEqual(result['failed'], 1)
        self.assertIn(self.item['url'], nt.load_failures(self.bdir))
        result, _ = self.fetch([self.item], '<div class="note-common-styles__textnote-body">Recovered</div>')
        self.assertEqual(result['saved'], 1)
        self.assertEqual(nt.load_failures(self.bdir), {})

    def test_429_stops(self):
        import urllib.error
        with patch.object(nt, 'Polite') as polite, patch.object(nt, 'read_rss', return_value=[self.item, dict(self.item, url='https://note.com/test/n/n456')]):
            polite.return_value.get.side_effect = urllib.error.HTTPError(self.item['url'], 429, 'limited', {}, None)
            result = nt.cmd_fetch(self.args)
            self.assertEqual(polite.return_value.get.call_count, 1)
            self.assertEqual(result['failed'], 1)

    def test_robots_failure_report(self):
        with patch.object(nt, 'Polite', side_effect=SystemExit('robots unavailable')):
            self.assertEqual(note_sync.run('test', self.root / 'report'), 1)
        result = json.loads((self.root / 'report/result.json').read_text())
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('last_checked_at', result)

    def test_no_change_report(self):
        with patch.object(nt, 'Polite'), patch.object(nt, 'read_rss', return_value=[]):
            self.assertEqual(note_sync.run('test', self.root / 'report'), 0)
        self.assertIn('last_checked_at', json.loads((self.root / 'report/result.json').read_text()))

    def test_robots_denied_and_interval(self):
        with patch.object(nt, 'http_get', return_value='User-agent: *\nDisallow: /private'), patch.object(nt.time, 'monotonic', return_value=10), patch.object(nt.time, 'sleep') as sleep:
            polite = nt.Polite(5)
            with self.assertRaises(PermissionError):
                polite.get('https://note.com/private')
            polite.get('https://note.com/test/rss')
            sleep.assert_called_once_with(5)

    def test_index_preserves_old_unsaved_rows(self):
        nt.write_index(self.bdir, [self.item])
        nt.write_index(self.bdir, [])
        self.assertIn(self.item['url'], (self.bdir / '記事一覧.csv').read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    unittest.main()
