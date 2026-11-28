import unittest
from unittest.mock import patch

from docx import Document
from docx.oxml import parse_xml

import cardformat
import learn
import settings
from organize import REVIEW, heading_topic

M = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'


def card(arg, tag, body, source, reason='Answer heading from the source'):
    return dict(arg=arg, tag=tag, body=body, read='', source=source, topic_reason=reason)


class LearnedSorterTests(unittest.TestCase):
    def library(self):
        rows = []
        for i in range(10):
            rows.append(card('Teacher Shortages', f'Teacher shortages are collapsing classrooms {i}',
                             'teachers quit schools shortage classroom staffing pay burnout', f'T{i % 3}'))
            rows.append(card('Tariffs', f'Tariffs raise consumer prices {i}',
                             'tariffs imports prices consumers trade war retaliation exports', f'S{i % 3}'))
        return rows

    def test_vague_cards_join_the_category_of_similar_cards(self):
        rows = self.library() + [
            card(REVIEW, 'Burnout makes teachers quit and leaves classrooms without staff',
                 'teachers burnout classroom staffing shortage schools quit pay ' * 3, 'X', 'Ambiguous'),
            card(REVIEW, 'Weather was nice', 'sunny day', 'X', 'Ambiguous')]
        moved = learn.learned_sort(rows)
        self.assertEqual(moved, 1)
        self.assertEqual(rows[-2]['arg'], 'Teacher Shortages')
        self.assertEqual(rows[-2]['topic_reason'], learn.LEARNED_REASON)
        self.assertEqual(rows[-1]['arg'], REVIEW)  # unsure cards stay put

    def test_headed_cards_are_never_moved(self):
        rows = self.library()
        before = [r['arg'] for r in rows]
        learn.learned_sort(rows)
        self.assertEqual([r['arg'] for r in rows], before)


class TheoryTests(unittest.TestCase):
    def test_theory_and_phil_hidden_in_pf_by_default(self):
        self.assertFalse(settings.show_theory(dict(settings.DEFAULTS, caselist='hspf26')))
        self.assertTrue(settings.show_theory(dict(settings.DEFAULTS, caselist='hsld26')))
        self.assertTrue(settings.show_theory(dict(settings.DEFAULTS, caselist='hspf26', show_theory=True)))

    def test_phil_headings_are_recognized(self):
        self.assertEqual(heading_topic('AT: Kant'), 'Framework')
        self.assertEqual(heading_topic('AT Spikes'), 'Theory and Topicality')
        self.assertEqual(heading_topic('AT: Alt'), 'Critiques')


class ProseEquationTests(unittest.TestCase):
    def test_sentence_pasted_as_an_equation_becomes_normal_text(self):
        doc = Document()
        doc.add_paragraph('The total cost of nuclear weapons programs is far greater.')
        p = doc.add_paragraph('Spending rose by US')
        p._p.append(parse_xml(f'<m:oMath {M}><m:r><m:t>1.4billionfrom2019.Thetotalcostofnuclearweaponsprogramsreached'
                              f'US</m:t></m:r></m:oMath>'))
        p.add_run('2 trillion.')
        text = ''.join(t for t, a, s in cardformat.pieces(p, 'body'))
        self.assertEqual(text, 'Spending rose by US$1.4 billion from 2019. The total cost of nuclear weapons '
                               'programs reached US$2 trillion.')

    def test_real_equations_are_left_alone(self):
        self.assertFalse(cardformat.prose_equation('E=mc2'))
        self.assertTrue(cardformat.prose_equation('1.4billionfrom2019,evengiven'))


class CitationTests(unittest.TestCase):
    def test_citation_keys(self):
        self.assertEqual(learn.citation_key('Smith 24 [John Smith, prof]'), 'a:smith24')
        self.assertEqual(learn.citation_key("Lee and Park '23, Foreign Affairs"), 'a:lee23')
        self.assertEqual(learn.citation_key('Doe 2021 https://www.example.org/a/b?utm=1'), 'u:example.org/a/b')

    def test_vague_card_follows_where_other_teams_filed_the_article(self):
        rows = [card('Readiness', 'Readiness is collapsing', 'military readiness gaps', s) for s in 'AB']
        for r in rows:
            r['cite'] = 'Kofman 24 [Michael Kofman]'
        vague = card(REVIEW, 'Their evidence is wrong', 'short', 'C', 'Ambiguous')
        vague['cite'] = 'Kofman 24 [Michael Kofman, CNA]'
        rows.append(vague)
        learn.learned_sort(rows)
        self.assertEqual(vague['arg'], 'Readiness')
        self.assertEqual(vague['topic_reason'], learn.CITE_REASON)


class CorrectionTests(unittest.TestCase):
    def test_moved_cards_stay_moved_and_teach_the_sorter(self):
        import tempfile
        from pathlib import Path
        import corrections
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(corrections, 'PATH', Path(folder) / 'corrections.json'):
                corrections.save('k1', 'AT NEG', 'Teacher Pay', 'Pay raises fix shortages')
                rows = [dict(arg=REVIEW, side='AT AFF', topic_reason='Ambiguous', key='k1'),
                        dict(arg='Economy', side='AT AFF', topic_reason='Recognized answer heading', key='k2')]
                self.assertEqual(corrections.apply(rows, lambda r: r['key']), 1)
                self.assertEqual((rows[0]['side'], rows[0]['arg'], rows[0]['topic_reason']),
                                 ('AT NEG', 'Teacher Pay', corrections.REASON))
                self.assertIn(corrections.REASON, learn.LABEL_REASONS)
                with self.assertRaises(ValueError):
                    corrections.save('k3', 'AT NEG', '   ')


class SharedTagTests(unittest.TestCase):
    def test_round_specific_tags_lose_to_general_ones(self):
        from organize import best_tag
        tags = ['Their whole case is wrong', '2] Deterrence fails because conventional forces are enough to stop Russia',
                'Conventional forces deter Russia, so nukes are unnecessary', 'Extend our Smith card']
        self.assertEqual(best_tag(tags), 'Deterrence fails because conventional forces are enough to stop Russia')
        self.assertEqual(best_tag(['Only one tag']), 'Only one tag')

    def test_merged_card_shows_shared_tag_and_hand_tag_wins(self):
        import dashboard
        body = 'Conventional forces are enough to deter Russia without nuclear weapons. ' * 3
        rows = [dict(id=i, tag=t, cite='Lee 26', body=body, marked=body, side='AT AFF', arg='Deterrence',
                     block='Defense / other', source=s, source_file=s + '.docx')
                for i, (t, s) in enumerate([('Their whole case is wrong', 'A'),
                                            ('Conventional forces deter Russia without nukes', 'B'),
                                            ('Conventional forces are enough to deter Russia', 'C')])]
        merged = dashboard._merged_cards([dict(r) for r in rows])
        self.assertEqual(len(merged), 1)
        self.assertNotEqual(merged[0]['shared_tag'] or merged[0]['tag'], 'Their whole case is wrong')
        rows[0]['custom_tag'] = 'My own tag'
        self.assertEqual(dashboard._merged_cards([dict(r) for r in rows])[0]['shared_tag'], 'My own tag')


if __name__ == '__main__':
    unittest.main()
