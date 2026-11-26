"""Export a current, deduplicated block library for Word or Google Docs.

No stored export snapshot: each request uses the current dashboard records.
Evidence comes exclusively from the original Word paragraphs.
"""
import io
import re
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.enum.text import WD_ALIGN_PARAGRAPH

from cardformat import HIGHLIGHTS, pieces, resolve

SIDE_ORDER = {'AT AFF': 0, 'AT NEG': 1}


def _source_paragraph(doc, original, role, response_number=None):
    # Resolve inherited source styles before writing into a shared document.
    # Copying source style IDs would cause collisions between different teams.
    if any(e.tag in (qn('w:drawing'), qn('w:pict'), qn('m:oMath')) for e in original._p.iter()):
        raise ValueError('A source card contains an image or equation. Export it individually to preserve that content.')
    p = doc.add_paragraph(style='Heading 3' if role == 'tag' else 'Normal')
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.keep_with_next = role in ('tag', 'cite')
    p.paragraph_format.keep_together = False
    skip=0
    if role=='tag' and response_number is not None:
        # Replace only the old list number, preserving the tag's actual wording
        # and run formatting, including when its number spans several runs.
        match=re.match(r'^\s*(?:\[\d+\]|\d+\s*[\]\).])\s*',original.text)
        skip=match.end() if match else 0
        prefix=p.add_run(f'{response_number}] ');prefix.bold=True;prefix.font.size=Pt(14)
    for text, a, size in pieces(original, role):
        if skip:
            removed=min(skip,len(text));text=text[removed:];skip-=removed
        if not text:continue
        r = p.add_run(text)
        r.font.name = a['font']; r.font.size = Pt(size)
        r.bold = a['bold'] or role == 'tag'
        r.italic = a['italic']; r.font.strike = a['strike']
        if a['underline']:
            u = OxmlElement('w:u'); u.set(qn('w:val'), a['underline']); r._r.get_or_add_rPr().append(u)
        if a['highlight']:
            h = OxmlElement('w:highlight')
            h.set(qn('w:val'), next(k for k, v in HIGHLIGHTS.items() if v == a['highlight']))
            r._r.get_or_add_rPr().append(h)
        if a['shading']:
            s = OxmlElement('w:shd'); s.set(qn('w:fill'), a['shading']); r._r.get_or_add_rPr().append(s)
        if a['color']: r.font.color.rgb = RGBColor.from_string(a['color'])
        if a.get('link'):
            link = OxmlElement('w:hyperlink')
            link.set(qn('r:id'), doc.part.relate_to(a['link'], RT.HYPERLINK, is_external=True))
            r._r.addprevious(link); link.append(r._r)
    return p


def library_docx(cards, topic, source_filter=''):
    if not cards: raise ValueError('There are no rebuttal cards to export.')
    doc = Document()
    for section in doc.sections:
        section.page_width = Inches(8.5); section.page_height = Inches(11)
        section.top_margin = section.bottom_margin = Inches(.7)
        section.left_margin = section.right_margin = Inches(.7)
    for name, size in [('Normal', 8), ('Title', 22), ('Heading 1', 18),
                       ('Heading 2', 16), ('Heading 3', 12), ('Heading 4', 14)]:
        s = doc.styles[name]; s.font.name = 'Arial'; s.font.size = Pt(size)
        s.font.color.rgb = RGBColor(0, 0, 0)
        s.paragraph_format.line_spacing = 1.05
        if name != 'Normal': s.paragraph_format.keep_with_next = True
        if s.element.pPr is not None:
            for border in list(s.element.pPr.findall(qn('w:pBdr'))): s.element.pPr.remove(border)
    stamp = datetime.now(ZoneInfo('America/Los_Angeles')).strftime('%B %d %Y at %I:%M %p PT')
    groups = defaultdict(list)
    for card in cards: groups[(card['side'], card['arg'])].append(card)
    previous_side = None
    for side, arg in sorted(groups, key=lambda x: (SIDE_ORDER.get(x[0], 99), x[0], x[1] == 'Needs review', x[1].casefold())):
        if side != previous_side:
            h = doc.add_paragraph(side, 'Title')
            h.alignment=WD_ALIGN_PARAGRAPH.CENTER
            h.paragraph_format.page_break_before = previous_side is not None
            doc.add_paragraph(topic)
            count=sum(len(group) for (s,_),group in groups.items() if s==side)
            doc.add_paragraph(f'{count} responses • Exported {stamp}')
            if source_filter:doc.add_paragraph('School or team filter: ' + source_filter)
        heading=arg if re.match(r'^AT\b',arg,re.I) else 'AT '+arg
        doc.add_heading(heading, 1)
        # Same order as the dashboard sections: response type, most-read, then tag without source numbering.
        from dashboard import RESPONSE_ORDER, _sort_text
        ordered = sorted(groups[(side, arg)], key=lambda c: (RESPONSE_ORDER.get(c['block'], 99), c['block'].casefold(),
                         -len(c.get('teams') or ()), _sort_text(c['tag']), c['source_file']))
        for number,card in enumerate(ordered,1):
            try:
                _, paragraphs = resolve(card)
                _source_paragraph(doc, paragraphs[0], 'tag',number)
                _source_paragraph(doc, paragraphs[1], 'cite')
                for paragraph in paragraphs[2:]: _source_paragraph(doc, paragraph, 'body')
            except ValueError as error:
                raise ValueError(f'Cannot export “{card["tag"]}”: {error}') from error
        previous_side = side
    buffer = io.BytesIO(); doc.save(buffer)
    return buffer.getvalue()


if __name__ == '__main__':
    import argparse
    from pathlib import Path
    import dashboard
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='', help='Optional school or team filter')
    parser.add_argument('--out', default='Rebuttal-Blocks.docx')
    parser.add_argument('--topic', default=__import__('settings').load()['topic'])
    args = parser.parse_args()
    data = library_docx(dashboard.cards_from_source(args.source), args.topic, args.source)
    Path(args.out).write_bytes(data)
    print(args.out)
