import io
import unittest
from unittest.mock import patch

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.enum.text import WD_ALIGN_PARAGRAPH

from library_export import library_docx


class LibraryExportTests(unittest.TestCase):
    def source(self):
        doc = Document()
        doc.add_paragraph('Exact original tag')
        doc.add_paragraph('Smith 26 [Exact credentials, publisher and URL]')
        p = doc.add_paragraph()
        p.add_run('Context before. ')
        r = p.add_run('Read this. '); r.font.highlight_color = WD_COLOR_INDEX.YELLOW; r.underline = True
        r = p.add_run('Bold context. '); r.bold = True
        link = OxmlElement('w:hyperlink')
        link.set(qn('r:id'), doc.part.relate_to('https://example.com/source', RT.HYPERLINK, is_external=True))
        r = OxmlElement('w:r'); t = OxmlElement('w:t'); t.text = 'Original link'; r.append(t); link.append(r); p._p.append(link)
        return doc, doc.paragraphs

    def card(self, **kw):
        return dict(tag='Exact original tag',source='Team_AB',source_file='Team_AB/round.docx',
                    side=kw.get('side','AT AFF'),arg=kw.get('arg','NATO and Alliances'),block='Link defense')

    def test_preserves_full_source_text_reading_marks_and_links(self):
        source, ps = self.source()
        with patch('library_export.resolve', return_value=(source, ps)):
            doc = Document(io.BytesIO(library_docx([self.card()], 'Topic')))
        texts = [p.text for p in doc.paragraphs]
        for original in ps[1:]: self.assertIn(original.text, texts)
        self.assertIn('1] '+ps[0].text,texts)
        self.assertFalse(any('Source:' in t or 'Team_AB/round.docx' in t for t in texts))
        p = next(p for p in doc.paragraphs if p.text == ps[-1].text)
        runs = list(p.iter_inner_content())
        self.assertEqual(p.runs[0].font.size.pt, 8)
        self.assertEqual(p.runs[1].font.size.pt, 11)
        self.assertEqual(p.runs[1].font.highlight_color, WD_COLOR_INDEX.YELLOW)
        self.assertTrue(p.runs[1].underline)
        self.assertTrue(p.runs[2].bold)
        self.assertEqual(p.runs[2].font.size.pt, 9.5)
        self.assertEqual(p.hyperlinks[0].url, 'https://example.com/source')
        self.assertEqual(doc.paragraphs[0].text,'AT AFF')
        self.assertEqual(doc.paragraphs[0].alignment,WD_ALIGN_PARAGRAPH.CENTER)
        self.assertEqual(doc.paragraphs[0].style.name,'Title')
        self.assertEqual([p.text for p in doc.paragraphs if p.style.name=='Heading 1'], ['AT NATO and Alliances'])
        self.assertEqual([p.text for p in doc.paragraphs if p.style.name=='Heading 3'], ['1] Exact original tag'])

    def test_export_is_fresh_and_never_silently_omits_unresolved_cards(self):
        source = self.source()
        with patch('library_export.resolve', return_value=source):
            first = Document(io.BytesIO(library_docx([self.card()], 'Topic')))
            second = Document(io.BytesIO(library_docx([self.card(), self.card(side='AT NEG',arg='Disarmament')], 'Topic')))
        self.assertEqual(sum(p.style.name=='Heading 3' for p in first.paragraphs), 1)
        self.assertEqual(sum(p.style.name=='Heading 3' for p in second.paragraphs), 2)
        with patch('library_export.resolve', side_effect=ValueError('Source missing')):
            broken = Document(io.BytesIO(library_docx([self.card()], 'Topic')))
        texts = [p.text for p in broken.paragraphs]
        self.assertIn('Cards that could not be exported', texts)  # listed, never silently dropped
        self.assertTrue(any('Exact original tag' in t and 'Source missing' in t for t in texts))
        with self.assertRaises(ValueError): library_docx([], 'Topic')

    def test_numbering_is_continuous_across_response_types_and_restarts_per_argument(self):
        source,ps=self.source()
        ps[0].clear()
        ps[0].add_run('9');ps[0].add_run('] Exact original tag')
        cards=[self.card(),dict(self.card(),block='Turn'),self.card(arg='Disarmament'),self.card(side='AT NEG')]
        with patch('library_export.resolve',return_value=(source,ps)):
            doc=Document(io.BytesIO(library_docx(cards,'Topic')))
        headings=[p.text for p in doc.paragraphs if p.style.name=='Heading 3']
        self.assertEqual(headings,['1] Exact original tag','1] Exact original tag','2] Exact original tag','1] Exact original tag'])
        titles=[p for p in doc.paragraphs if p.style.name=='Title']
        self.assertEqual([p.text for p in titles],['AT AFF','AT NEG'])
        self.assertTrue(titles[1].paragraph_format.page_break_before)


    def test_images_and_equations_are_copied_not_refused(self):
        import struct, zlib
        from docx.shared import Inches
        def png():
            raw = b'\x00\xff\x00\x00' * 1  # one red pixel
            def chunk(kind, data):
                return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
            return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
                    + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
        source, ps = self.source()
        ps[-1].add_run().add_picture(io.BytesIO(png()), width=Inches(1))
        math = OxmlElement('m:oMath'); r = OxmlElement('m:r'); t = OxmlElement('m:t'); t.text = 'E=mc^2'
        r.append(t); math.append(r); ps[-1]._p.append(math)
        with patch('library_export.resolve', return_value=(source, source.paragraphs)):
            data = library_docx([self.card(), self.card(arg='Disarmament')], 'Topic')
        doc = Document(io.BytesIO(data))  # the file opens again: nothing broken
        self.assertEqual(len(doc.inline_shapes), 2)  # one picture per card
        self.assertTrue(all(s._inline.graphic.graphicData.pic.blipFill.blip.embed in doc.part.related_parts for s in doc.inline_shapes))
        equations = doc.element.body.findall('.//' + qn('m:oMath'))
        self.assertEqual(len(equations), 2)
        self.assertEqual(''.join(equations[0].itertext()), 'E=mc^2')
        ids = [e.get('id') for e in doc.element.body.iter(qn('wp:docPr'))]
        self.assertEqual(len(ids), len(set(ids)))  # no clashing picture ids


if __name__ == '__main__': unittest.main()
