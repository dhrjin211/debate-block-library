import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.enum.style import WD_STYLE_TYPE
from docx.shared import Pt

import cardformat as cf
from organize import classify,heading_topic,display_title,veto,RULES,REVIEW


class OrganizationTests(unittest.TestCase):
    def c(self, **kw):
        d=dict(tag='Claim',cite='Author 26',arg='',block='',body='',read='');d.update(kw);return d

    def test_energy_heading_and_turn_marker(self):
        h='T: Data centers provide vital innovation and back up energy'
        self.assertEqual(heading_topic(h),'Energy')
        import cardgrab
        self.assertEqual(cardgrab._response_type(self.c(tag='2] T: Clean energy solves warming')), 'Turn')
        self.assertEqual(cardgrab._response_type(self.c(tag='LT. Energy prices fall')), 'Turn')

    def test_article_cannot_become_topic(self):
        text='Big Tech data centre building creates carbon emissions from gas-fired power plants. '*80
        c=self.c(arg=text,tag='Electricity demand requires fossil fuels',body=text)
        topic,_,review=classify(c)
        self.assertEqual(topic,'Energy');self.assertFalse(review)
        self.assertIn(topic,RULES)

    def test_card_vetoes_stale_sentence_heading(self):
        c=self.c(arg="Orbital data centers can't be constructed and don't solve status quo",tag='China hates the plan',body='Chinese officials oppose the moratorium and China rejects it.')
        self.assertEqual(classify(c)[0],'China and Taiwan')

    def test_explicit_target_survives_incidental_impact(self):
        c=self.c(arg='AT: Economy',tag='No causal link',body='Climate and energy policy affect China.')
        self.assertEqual(classify(c)[0],'Economy')
        self.assertEqual(heading_topic('2AC---AT Econ'),'Economy')
        self.assertEqual(heading_topic('2NC---Climate'),'Climate')

    def test_placeholder_and_long_title(self):
        c=self.c(tag='Placeholder 21',body='Some evidence')
        self.assertIsNotNone(veto(c))
        self.assertLessEqual(len(display_title('Long claim '*70)),150)
        self.assertEqual(classify(self.c())[0],REVIEW)


class FormattingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        (self.base/'docs').mkdir()
        self.doc=Document()
        self.doc.add_heading('A real debate tag',4)
        self.doc.add_paragraph('White 26 [Reporter, source title, https://example.com]')
        p=self.doc.add_paragraph()
        p.add_run('Plain ')
        p.add_run('Underlined ').underline=True
        p.add_run('Bold ').bold=True
        r=p.add_run('Bold underlined ');r.bold=True;r.underline=True
        r=p.add_run('Yellow ');r.font.highlight_color=WD_COLOR_INDEX.YELLOW
        r=p.add_run('Green ');r.font.highlight_color=WD_COLOR_INDEX.BRIGHT_GREEN
        r=p.add_run('Italic');r.italic=True
        self.doc.save(self.base/'docs'/'sample.docx')
        self.card=dict(tag='A real debate tag',cite=self.doc.paragraphs[1].text,
                       body=self.doc.paragraphs[2].text,read='',source_file='sample.docx')
        self.patch=patch.object(cf,'BASE',self.base);self.patch.start()

    def tearDown(self):
        self.patch.stop();cf.source_cards.cache_clear();self.temp.cleanup()

    def test_docx_keeps_each_style_and_all_text(self):
        output=Document(io.BytesIO(cf.docx_bytes(self.card)))
        self.assertEqual([p.text for p in output.paragraphs],[p.text for p in self.doc.paragraphs])
        runs=output.paragraphs[2].runs
        self.assertEqual([r.font.size.pt for r in runs],[8,11,9.5,9.5,11,11,8])
        self.assertIsNone(runs[0].underline)
        self.assertTrue(runs[1].underline)
        self.assertIsNone(runs[1].font.highlight_color)
        self.assertEqual(runs[4].font.highlight_color,WD_COLOR_INDEX.YELLOW)
        self.assertEqual(runs[5].font.highlight_color,WD_COLOR_INDEX.BRIGHT_GREEN)
        self.assertTrue(runs[3].bold);self.assertTrue(runs[3].underline);self.assertTrue(runs[6].italic)
        self.assertEqual(output.paragraphs[0].runs[0].font.size.pt,14)
        cite=output.paragraphs[1].runs
        self.assertEqual(cite[0].text,'White 26');self.assertEqual(cite[0].font.size.pt,14)
        self.assertEqual(cite[1].font.size.pt,8)

    def test_html_does_not_turn_underline_into_highlight(self):
        data=cf.formatted(self.card)
        self.assertIn('background-color:#FFFF00',data['body_html'])
        self.assertIn('background-color:#00FF00',data['body_html'])
        from html.parser import HTMLParser
        class Spans(HTMLParser):
            def __init__(self):super().__init__();self.spans={};self.style=''
            def handle_starttag(self,tag,attrs):
                if tag=='span':self.style=dict(attrs).get('style','')
            def handle_data(self,data):self.spans[data]=self.style
        parser=Spans();parser.feed(data['body_html'])
        self.assertIn('underline',parser.spans['Underlined '])
        self.assertNotIn('background-color',parser.spans['Underlined '])
        self.assertIn('font-size:9.5pt',parser.spans['Bold underlined '])
        self.assertIn(self.card['body'],data['plain_text'])

    def test_inherited_style_is_kept(self):
        style=self.doc.styles.add_style('Read style',WD_STYLE_TYPE.CHARACTER)
        style.font.bold=True;style.font.underline=True;style.font.highlight_color=WD_COLOR_INDEX.TURQUOISE
        self.doc.paragraphs[2].runs[0].style=style
        self.doc.save(self.base/'docs'/'sample.docx')
        data=cf.formatted(self.card)
        self.assertIn('background-color:#00FFFF',data['body_html'])
        output=Document(io.BytesIO(cf.docx_bytes(self.card)))
        self.assertEqual(output.paragraphs[2].runs[0].font.size.pt,11)
        self.assertTrue(output.paragraphs[2].runs[0].style.font.bold)

    def test_duplicate_source_cuts_use_ingest_occurrence(self):
        import sqlite3, cardgrab
        p=self.doc.add_paragraph('A real debate tag','Heading 4')
        self.doc.add_paragraph(self.card['cite'])
        from copy import deepcopy
        self.doc._element.body.insert(len(self.doc._element.body)-1,deepcopy(self.doc.paragraphs[2]._p))
        self.doc.paragraphs[-1].runs[0].bold=True
        self.doc.save(self.base/'docs'/'sample.docx')
        db=self.base/'sample.db';con=sqlite3.connect(db)
        con.execute('create table cards (source_file,tag,cite,body,speech,arg_heading,block_heading)')
        values=(self.card['source_file'],self.card['tag'],self.card['cite'],self.card['body'],'A/2 / AT heading','','')
        con.executemany('insert into cards values (?,?,?,?,?,?,?)',[values,values]);con.commit();con.close()
        self.card.update(id=1,speech='A/2 / AT heading',arg_heading='',block_heading='')
        with patch.object(cardgrab,'DB',str(db)):
            first=Document(io.BytesIO(cf.docx_bytes(self.card)))
            self.card['id']=2
            second=Document(io.BytesIO(cf.docx_bytes(self.card)))
        self.assertEqual(first.paragraphs[2].runs[0].font.size.pt,8)
        self.assertEqual(second.paragraphs[2].runs[0].font.size.pt,9.5)

    def test_citation_accidentally_styled_as_tag_is_recovered(self):
        import cardgrab
        self.doc.paragraphs[1].style=self.doc.styles['Heading 4']
        self.doc.save(self.base/'docs'/'sample.docx')
        parsed=cardgrab.parse(self.base/'docs'/'sample.docx')
        self.assertEqual(len(parsed),1)
        self.assertEqual(parsed[0]['tag'],self.card['tag'])
        self.assertEqual(parsed[0]['cite'],self.card['cite'])
        cf.resolve(self.card)

    def test_missing_source_fails_without_fake_formatting(self):
        self.card['source_file']='missing.docx'
        with self.assertRaisesRegex(ValueError,'unavailable'):cf.formatted(self.card)
        with self.assertRaises(ValueError):cf.docx_bytes(self.card)

    def test_path_cannot_escape_source_directory(self):
        self.card['source_file']='../outside.docx'
        with self.assertRaises(ValueError):cf.resolve(self.card)


if __name__=='__main__':unittest.main()
