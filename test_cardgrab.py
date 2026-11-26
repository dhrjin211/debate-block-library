import tempfile
import unittest
from pathlib import Path
from docx import Document
import cardgrab as cg
import dashboard as dash


def card(**kwargs):
    return dict(tag='[NL] No causal link', cite='Author 2026', body=['China uniquely turns to renewables.'] * 10,
                speech='Rebuttal', arg='AT: Economy', block='', **kwargs)


class ClassificationTests(unittest.TestCase):
    def test_all_evidence_mode_still_rejects_invalid_cards(self):
        c = card()
        c.update(speech='1AC', arg='Nuclear Deterrence')
        self.assertIsNotNone(cg.label(c, {}, True, 'Team-Pro-Round.docx'))
        c['cite'] = ''
        self.assertIsNone(cg.label(c, {}, True, 'Team-Pro-Round.docx'))
        c.update(cite='Author 2026', tag='<< INSERTED >>')
        self.assertIsNone(cg.label(c, {}, True, 'Team-Pro-Round.docx'))

    def test_response_filter(self):
        for speech in ('1AC', '1NC', 'Constructive', 'Case', 'Blocks', ''):
            c = card(); c.update(speech=speech, arg='Economy')
            self.assertIsNone(cg.label(c, {}, False))
        for speech in ('Rebuttal', '2AC', '2NC', '1AR', '2NR'):
            c = card(); c['speech'] = speech
            self.assertIsNotNone(cg.label(c, {}, False))
        c = card(); c['speech'] = ''
        self.assertIsNotNone(cg.label(c, {}, False))

    def test_target_and_type_do_not_follow_body_keywords(self):
        c = card()
        self.assertEqual(cg.label(c, {}, False)[1:], ('Economy', 'Link defense'))
        c.update(arg='AT: Environment', block='AT: Water')
        self.assertEqual(cg._topic(c), 'Water')
        c.update(arg='', block='')
        self.assertEqual(cg._topic(c), 'Unsorted')
        c['tag'] = '[DL/T] This is non-unique'
        self.assertEqual(cg._response_type(c), 'Turn')
        c.update(tag='Evidence claim', arg='AT: Economy', block='Uniqueness')
        self.assertEqual(cg._response_type(c), 'Uniqueness')

    def test_heading_aliases_and_duplicates(self):
        for value in ('AT: Econ', 'AT: Economy Economy', 'AT: Economy AT: Economy'):
            self.assertEqual(cg._clean_topic(value), 'Economy')
        self.assertNotEqual(cg._clean_topic('Water'), cg._clean_topic('Climate'))

    def test_hierarchy_and_speech_reset(self):
        doc = Document()
        def add(text, level): doc.add_heading(text, level)
        def evidence():
            add('A response', 4); doc.add_paragraph('Author 2026'); doc.add_paragraph('Evidence body')
        add('2AC', 1); add('AT: Economy', 1); add('Link defense', 3); evidence()
        add('AT: Water', 1); evidence()
        add('Constructive', 1); evidence()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'sample.docx'; doc.save(path)
            rows = cg.parse(path)
        self.assertEqual(rows[0]['speech'], '2AC')
        self.assertEqual(cg._topic(rows[0]), 'Economy')
        self.assertEqual(cg._topic(rows[1]), 'Water')
        self.assertIsNone(cg.label(rows[2], {}, False))


class DedupTests(unittest.TestCase):
    def row(self, **kwargs):
        r = dict(id=1, side='AT AFF', arg='Economy', block='Link defense',
                 tag='1] No link', cite='Author 2026', body='Evidence text with meaning. ' * 20,
                 marked='Evidence text with meaning. ' * 20, source='Team A')
        r.update(kwargs); return r

    def test_duplicate_tag_and_type_are_one_card(self):
        rows = [self.row(), self.row(id=2, tag='No link', block='Defense / other', source='Team B')]
        merged = dash._merged_cards(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['block'], 'Link defense')
        self.assertEqual(len(merged[0]['teams']), 2)
        self.assertEqual(len(dash._merged_cards(merged)), 1)

    def test_same_evidence_shows_once_under_most_common_argument(self):
        rows = [self.row(), self.row(id=2, arg='Water'), self.row(id=4, arg='Water', source='Team C'),
                self.row(id=3, arg='Other / unclear')]
        merged = dash._merged_cards(rows)
        self.assertEqual([r['arg'] for r in merged], ['Water'])
        self.assertEqual(len(dash.pack(merged)), 1)

    def test_short_evidence_ignores_tag_number(self):
        rows = [self.row(body='Short evidence', marked='Short evidence'),
                self.row(id=2, tag='No link', body='Short evidence', marked='Short evidence')]
        self.assertEqual(len(dash._merged_cards(rows)), 1)

    def test_search_uses_words_and_retains_argument_uses(self):
        from unittest.mock import patch
        rows = [self.row(tag='fails', arg='Economy'), self.row(id=2, tag='AI', arg='Water')]
        with patch.object(dash, '_all_cards', return_value=dash._merged_cards(rows)):
            found = dash.search('AI')
            self.assertEqual([r['id'] for r in found], [2])

    def test_changed_excerpt_not_merged(self):
        rows = [self.row(), self.row(id=2, body='Different excerpt', marked='Different excerpt')]
        self.assertEqual(len(dash._merged_cards(rows)), 2)

    def test_same_spoken_cut_keeps_fuller_source_context(self):
        speech = 'The same evidence selected for speech. ' * 6
        cite = 'Author 2026 https://example.org/article'
        rows = [self.row(read=speech, cite=cite, body=speech, marked=speech),
                self.row(id=2, read=speech, cite=cite, body='Context. '+speech+' Further context.',
                         marked='Context. '+speech+' Further context.', source='Team B')]
        merged = dash._merged_cards(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['id'], 2)
        self.assertEqual(merged[0]['teams'], {'Team A', 'Team B'})
        # A different highlighting of the same article and excerpt is still one card.
        rows[1]['read'] = 'A different spoken selection. ' * 6
        self.assertEqual(len(dash._merged_cards(rows)), 1)

    def test_overlapping_cuts_of_same_article_merge(self):
        cite = 'Author 2026 https://example.org/article'
        words = ' '.join(f'word{i}' for i in range(300))
        rows = [self.row(cite=cite, body=words, marked=words),
                self.row(id=2, cite=cite, body=words + ' extra tail text', marked=words + ' extra tail text',
                         source='Team B'),
                self.row(id=3, cite=cite, body='Context. ' + words[:-40], marked='Context. ' + words[:-40],
                         source='Team C')]
        merged = dash._merged_cards(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['teams'], {'Team A', 'Team B', 'Team C'})

    def test_different_excerpts_of_same_article_stay_separate(self):
        cite = 'Author 2026 https://example.org/article'
        first = ' '.join(f'alpha{i}' for i in range(200))
        second = ' '.join(f'beta{i}' for i in range(200))
        rows = [self.row(cite=cite, body=first, marked=first),
                self.row(id=2, cite=cite, body=second, marked=second)]
        self.assertEqual(len(dash._merged_cards(rows)), 2)

    def test_same_text_in_different_sections_is_not_repeated(self):
        rows = [self.row(), self.row(id=2, arg='Water', source='Team B'), self.row(id=3, side='AT NEG', source='Team C')]
        merged = dash._merged_cards(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]['teams']), 3)

    def test_section_order_ignores_source_numbering(self):
        self.assertEqual(dash.clean_tag('2] Russia is defensive'), 'Russia is defensive')
        self.assertEqual(dash.clean_tag('--nuclear deterrence fails'), 'nuclear deterrence fails')
        self.assertEqual(dash.clean_tag('4. US deterrence is sufficient'), 'US deterrence is sufficient')
        self.assertEqual(dash.clean_tag('3.5 million troops'), '3.5 million troops')
        self.assertEqual(dash.clean_tag('2026 was a record year'), '2026 was a record year')
        rows = [self.row(id=1, tag='2] Zebra', body='z ' * 80, marked='z ' * 80),
                self.row(id=2, tag='Apple', body='a ' * 80, marked='a ' * 80),
                self.row(id=3, tag='--mango', body='m ' * 80, marked='m ' * 80, cite='Other 2025')]
        packed = dash.pack(dash._merged_cards(rows))
        self.assertEqual([c['display_tag'] for c in packed], ['Apple', 'mango', 'Zebra'])
        self.assertNotIn('body', packed[0])
        self.assertIn('preview', packed[0])

    def test_more_teams_sort_first(self):
        rows = [self.row(id=1, tag='Apple', body='a ' * 80, marked='a ' * 80),
                self.row(id=2, tag='Zebra', body='z ' * 80, marked='z ' * 80),
                self.row(id=3, tag='Zebra', body='z ' * 80, marked='z ' * 80, source='Team B')]
        self.assertEqual([c['tag'] for c in dash.pack(dash._merged_cards(rows))], ['Zebra', 'Apple'])

    def test_no_visible_ranking_or_added_number(self):
        self.assertNotIn('Score:', dash.PAGE)
        self.assertNotIn('${i+1}.', dash.PAGE)


if __name__ == '__main__': unittest.main()


class SharedHeadingTests(unittest.TestCase):
    def test_one_team_heading_goes_to_unsorted_but_shared_heading_stays(self):
        rows = [dict(arg='Action', topic_reason='Answer heading from the source', source='Team A'),
                dict(arg='TikTok Ban', topic_reason='Answer heading from the source', source='Team A'),
                dict(arg='TikTok Ban', topic_reason='Short source heading', source='Team B'),
                dict(arg='Economy', topic_reason='Recognized answer heading', source='Team A')]
        dash._keep_shared_heading_categories(rows)
        self.assertEqual([r['arg'] for r in rows], [dash.REVIEW, 'TikTok Ban', 'TikTok Ban', 'Economy'])


class IngestTests(unittest.TestCase):
    """Reads real .docx files end to end, then checks the saved results are reused."""

    def make(self, path, answer, tag):
        from docx import Document
        d = Document()
        d.add_heading('2AC', 1)
        d.add_heading(answer, 2)
        d.add_heading(tag, 4)
        d.add_paragraph('Lee 26 [Ann Lee, analyst, https://example.com/a]')
        d.add_paragraph('Evidence text that explains why the argument fails because of several reasons. ' * 3)
        path.parent.mkdir(parents=True, exist_ok=True)
        d.save(path)

    def test_ingest_reads_once_then_reuses_results(self):
        import io, sqlite3, tempfile
        from contextlib import redirect_stdout
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            docs = tmp / 'docs'
            self.make(docs / 'TeamA' / 'a.docx', 'AT: Deterrence', 'Deterrence fails')
            with patch.object(cg, 'DB', str(tmp / 'cards.db')), patch.object(cg, 'CACHE', tmp / 'cache.db'):
                def run():
                    out = io.StringIO()
                    with redirect_stdout(out):
                        cg.ingest(str(docs))
                    return out.getvalue()
                first = run()
                self.assertIn('Reading 1 file.', first)
                self.assertRegex(first, r'\b1 (response )?cards stored')
                self.assertIn('Nothing changed', run())
                self.make(docs / 'TeamB' / 'b.docx', 'AT: NATO', 'NATO is strong')
                third = run()
                self.assertIn('reading 1 new or changed file.', third)
                self.assertRegex(third, r'\b2 (response )?cards stored')
                rows = sqlite3.connect(tmp / 'cards.db').execute('SELECT arg, source, reason FROM cards ORDER BY source').fetchall()
                self.assertEqual([r[:2] for r in rows], [('Nuclear Deterrence', 'TeamA'), ('NATO and Alliances', 'TeamB')])
                self.assertTrue(all(r[2] for r in rows))
