import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from docx import Document
from docx.enum.text import WD_COLOR_INDEX
import cardgrab
import cardformat


class SourceLayoutTests(unittest.TestCase):
    def test_explicit_answer_in_an_unlabeled_source_is_retained(self):
        c=dict(tag='No nuclear terrorism: barriers prevent it.',cite='Author 26',body=['Source evidence'],read=[],speech='',arg='',block='')
        self.assertIsNotNone(cardgrab.label(c,{},False,'Team/Team-Con-Round-2.docx'))
        c['speech']='1NC'
        self.assertIsNone(cardgrab.label(c,{},False,'Team/Team-Con-Round-2.docx'))

    def test_plain_rebuttal_and_tags_retain_the_original_paragraphs(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);(base/'docs').mkdir()
            doc=Document()
            doc.add_paragraph('2AC','Title')
            doc.add_paragraph('AT: Deterrence')
            doc.add_paragraph('Conventional defense solves.')
            doc.add_paragraph('Smith 26 [Security researcher, https://example.com/report]')
            p=doc.add_paragraph('Original context. ')
            r=p.add_run('Selected evidence.');r.bold=True;r.underline=True;r.font.highlight_color=WD_COLOR_INDEX.YELLOW
            path=base/'docs'/'source.docx';doc.save(path)
            cards=cardgrab.parse(path)
            self.assertEqual(len(cards),1)
            self.assertEqual(cardgrab.label(cards[0],{},False)[1],'Nuclear Deterrence')
            c=cards[0];c.update(body=' '.join(c['body']),read=' '.join(c['read']),source_file='source.docx')
            with patch.object(cardformat,'BASE',base):
                html=cardformat.formatted(c)
            self.assertIn('Original context. ',html['plain_text'])
            self.assertIn('background-color:#FFFF00',html['body_html'])
            self.assertIn('text-decoration-line:underline',html['body_html'])

    def test_table_evidence_and_next_card_do_not_merge(self):
        with tempfile.TemporaryDirectory() as td:
            doc=Document();doc.add_paragraph('2NC')
            cell=doc.add_table(rows=1,cols=1).cell(0,0)
            cell.paragraphs[0].text='The treaty lacks enforcement.'
            cell.add_paragraph('Author 26 [https://example.com/one]')
            cell.add_paragraph('Full first source context.')
            doc.add_paragraph('Verification is inadequate.')
            doc.add_paragraph('Other 26 [https://example.com/two]')
            doc.add_paragraph('Full second source context.')
            path=Path(td)/'table.docx';doc.save(path)
            cards=cardgrab.parse(path)
            self.assertEqual([c['body'] for c in cards],[['Full first source context.'],['Full second source context.']])
            self.assertTrue(all(cardgrab.label(c,{},False) for c in cards))

    def test_source_accidentally_all_heading_four_keeps_context(self):
        with tempfile.TemporaryDirectory() as td:
            doc=Document();doc.add_paragraph('2NC')
            for text in ['The treaty fails.','Author 2026 [https://example.com]',
                         'First paragraph of full context.','A second paragraph preserves the qualification.']:
                doc.add_paragraph(text,'Heading 4')
            path=Path(td)/'heading.docx';doc.save(path)
            cards=cardgrab.parse(path)
            self.assertEqual(len(cards),1)
            self.assertEqual(len(cards[0]['body']),2)


if __name__=='__main__':unittest.main()
