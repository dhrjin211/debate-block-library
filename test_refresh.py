import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import refresh
import settings
from organize import classify, heading_label


def docx_bytes(text):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return buffer.getvalue()


class AnyTopicTests(unittest.TestCase):
    def test_unknown_answer_heading_becomes_its_own_category(self):
        card = dict(tag='Bans fail', cite='Lee 26', body='Some evidence text', read='', block='', arg='AT: universal basic income')
        self.assertEqual(classify(card)[0], 'Universal Basic Income')

    def test_labels_reject_sentences_and_generic_words(self):
        self.assertEqual(heading_label('A2 Kaliningrad Corridor---2AC'), 'Kaliningrad Corridor')
        self.assertEqual(heading_label('AT: Turns'), '')
        self.assertEqual(heading_label('Their evidence is old and does not say what they claim'), '')


class TopicDateTests(unittest.TestCase):
    def test_current_topic_follows_the_release_calendar(self):
        from datetime import date
        self.assertEqual(settings.current_topic('hspf26', date(2026, 11, 26)), ('November–December 2026 Public Forum', date(2026, 10, 1)))
        self.assertEqual(settings.current_topic('hspf26', date(2026, 12, 1))[0], 'January 2027 Public Forum')
        self.assertEqual(settings.current_topic('hsld26', date(2027, 3, 1)), ('March–April 2027 Lincoln-Douglas', date(2027, 2, 1)))


class AutoTopicTests(unittest.TestCase):
    words = ['european', 'nato', 'members', 'ratify', 'treaty', 'prohibition', 'nuclear', 'weapons']

    def test_topic_name_filters_old_topic_files(self):
        new = docx_bytes('NATO members ratify the treaty on the prohibition of nuclear weapons. ' * 5 + 'other words ' * 100)
        old = docx_bytes('Data centers need nuclear energy and members of congress agree. ' * 5 + 'other words ' * 100)
        self.assertTrue(refresh._on_topic(new, [], self.words))
        self.assertFalse(refresh._on_topic(old, [], self.words))
        self.assertTrue(refresh._on_topic(old, [], []))  # no topic name: keep everything

    def test_topic_words_skip_resolution_filler(self):
        with patch.object(settings, 'load', lambda: dict(settings.DEFAULTS, topic='Resolved: The United States federal government should ban biometric surveillance through data collection')):
            self.assertEqual(settings.topic_words(), ['biometric', 'surveillance', 'data', 'collection'])


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        self.patches = [patch.object(refresh, 'DOCS', base / 'docs'),
                        patch.object(refresh, 'DOWNLOADED', base / 'docs' / 'OpenCaselist'),
                        patch.object(refresh, 'UPLOADED', base / 'docs' / 'Uploaded'),
                        patch.object(settings, 'PATH', base / 'settings.json')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.dir.cleanup()

    def test_choose_only_new_weekly_bundles_since_date(self):
        downloads = [dict(name='hspf26-weekly-2026-09-28.zip', url='u1'), dict(name='hspf26-weekly-2026-10-05.zip', url='u2'),
                     dict(name='hspf26-weekly-2026-10-12.zip', url='u3'), dict(name='hspf26-all-2026-10-12.zip', url='u4')]
        chosen = refresh.choose(downloads, '2026-10-01', {'hspf26-weekly-2026-10-05.zip'})
        self.assertEqual([c['name'] for c in chosen], ['hspf26-weekly-2026-10-12.zip'])

    def test_keyword_mentions(self):
        self.assertEqual(refresh.mentions(docx_bytes('TPNW ratification. TPNW members. tpnw'), ['TPNW']), 3)
        self.assertIsNone(refresh.mentions(docx_bytes('anything'), []))

    def test_upload_and_delete_month(self):
        name = refresh.save_upload('../../Evil Name.docx', docx_bytes('hi'))
        self.assertEqual(name, 'Evil Name.docx')
        rows = refresh.months()
        self.assertEqual(rows[0]['files'], 1)
        self.assertEqual(refresh.delete_month('Uploaded', rows[0]['month']), 1)
        self.assertEqual(refresh.months(), [])
        with self.assertRaises(ValueError):
            refresh.delete_month('Uploaded', '../..')
        with self.assertRaises(ValueError):
            refresh.save_upload('notes.txt', b'x')

    def test_settings_accept_pasted_caselist_link(self):
        self.assertEqual(settings.save({'caselist': 'https://opencaselist.com/hspf26/SomeSchool'})['caselist'], 'hspf26')
        with self.assertRaises(ValueError):
            settings.save({'since': 'last week'})
        auto = settings.current_topic('hspf26')[1].isoformat()
        self.assertEqual(settings.save({'since': auto})['since'], '')  # stays automatic
        self.assertEqual(settings.since(), auto)
        self.assertEqual(settings.save({'since': '2026-09-15'})['since'], '2026-09-15')


if __name__ == '__main__':
    unittest.main()


class FakeResponse:
    def __init__(self, status=200, data=None, content=b''):
        self.status_code, self._data, self.content = status, data, content
    def json(self): return self._data
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(self.status_code)
    def iter_content(self, size): yield self.content
    def __enter__(self): return self
    def __exit__(self, *a): pass


class DownloadTests(RefreshTests):
    def test_download_sorts_by_month_filters_topic_and_never_repeats(self):
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, 'w') as z:
            z.writestr('hspf26/School/AB/aff-round1.docx', docx_bytes('TPNW TPNW TPNW ratification'))
            z.writestr('hspf26/School/AB/old-topic.docx', docx_bytes('data centers'))
        bundle = bundle.getvalue()
        listing = [dict(name='hspf26-weekly-2026-11-02.zip', url='https://files/x.zip')]

        class Session:
            def post(self, url, **kw): return FakeResponse(200 if kw['json']['password'] == 'right' else 401)
            def get(self, url, **kw): return FakeResponse(data=listing) if url.endswith('/downloads') else FakeResponse(content=bundle)

        settings.save({'caselist': 'hspf26', 'since': '2026-10-01', 'keywords': 'TPNW'})
        with patch.object(refresh, 'HISTORY', Path(self.dir.name) / 'history.json'), patch.object(refresh.requests, 'Session', Session):
            with self.assertRaises(ValueError):
                refresh.download('me@example.com', 'wrong', log=lambda *a: None)
            self.assertEqual(refresh.download('me@example.com', 'right', log=lambda *a: None), 1)
            saved = list((refresh.DOWNLOADED / '2026-11').rglob('*.docx'))
            self.assertEqual([f.name for f in saved], ['aff-round1.docx'])
            self.assertEqual(refresh.delete_month('OpenCaselist', '2026-11'), 1)
            self.assertEqual(refresh.download('me@example.com', 'right', log=lambda *a: None), 0)


class NightBeforeTests(RefreshTests):
    def test_files_after_last_bundle_are_fetched_once_and_updates_replace_old_copy(self):
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, 'w') as z:
            z.writestr('hspf26/School/AB/case.docx', docx_bytes('TPNW TPNW TPNW'))
        bundle = bundle.getvalue()
        state = {'recent': []}
        calls = []

        class Session:
            def post(self, url, **kw): return FakeResponse(200)
            def get(self, url, **kw):
                calls.append(url)
                if url.endswith('/downloads'):
                    return FakeResponse(data=[dict(name='hspf26-weekly-2026-11-02.zip', url='https://files/x.zip')])
                if url.endswith('/recent'):
                    return FakeResponse(data=state['recent'])
                if url.endswith('/download'):
                    return FakeResponse(content=docx_bytes('TPNW TPNW TPNW updated'))
                return FakeResponse(content=bundle)

        settings.save({'caselist': 'hspf26', 'since': '2026-10-01'})
        quiet = lambda *a: None
        with patch.object(refresh, 'HISTORY', Path(self.dir.name) / 'history.json'), patch.object(refresh.requests, 'Session', Session):
            self.assertEqual(refresh.download('e', 'p', quiet, pause=0), 1)
            # Night before the tournament: the same team updated its case after the bundle.
            state['recent'] = [dict(opensource='hspf26/School/AB/case.docx', updated_at='2026-12-04 21:00:00'),
                               dict(opensource='hspf26/School/AB/old.docx', updated_at='2026-10-30 10:00:00')]
            self.assertEqual(refresh.download('e', 'p', quiet, pause=0), 1)
            copies = list(refresh.DOWNLOADED.rglob('case.docx'))
            self.assertEqual([c.parent.parent.name for c in copies], ['2026-12'])  # old November copy replaced
            calls.clear()
            self.assertEqual(refresh.download('e', 'p', quiet, pause=0), 0)  # nothing new, nothing re-downloaded
            self.assertFalse(any(c.endswith('/download') or c.endswith('x.zip') for c in calls))
