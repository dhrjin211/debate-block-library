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


if __name__ == '__main__':
    unittest.main()
