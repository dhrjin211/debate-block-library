"""Resolve cards back to original DOCX paragraphs; preserve distinct Word run styles.

The old marked column cannot distinguish underline from highlight. It is never
used to synthesize a formatted export. Missing/ambiguous source matches fail clearly.
"""
import io
import re
from copy import deepcopy
from functools import lru_cache
from html import escape
from pathlib import Path
from threading import Lock

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from docx.text.run import Run
from organize import looks_like_citation

BASE = Path(__file__).resolve().parent
HIGHLIGHTS = {'black':'000000','blue':'0000FF','cyan':'00FFFF','green':'00FF00','magenta':'FF00FF',
 'red':'FF0000','yellow':'FFFF00','white':'FFFFFF','darkBlue':'000080','darkCyan':'008080',
 'darkGreen':'008000','darkMagenta':'800080','darkRed':'800000','darkYellow':'808000',
 'darkGray':'808080','lightGray':'C0C0C0'}


def norm(text):
    return re.sub(r'\s+',' ', text or '').strip()


def is_prose_heading(p):
    return p.style.name in {'Heading 1','Heading 2','Heading 3'} and (len(p.text)>700 or len(p.text.split())>100)


_PARSE_LOCKS = {}
_PARSE_LOCKS_GUARD = Lock()


def source_cards(path_string, modified):
    # One parse per file at a time: parallel requests for the same source wait
    # and then reuse the cached result instead of parsing it again.
    with _PARSE_LOCKS_GUARD:
        lock = _PARSE_LOCKS.setdefault(path_string, Lock())
    with lock:
        return _source_cards(path_string, modified)


source_cards.cache_clear = lambda: _source_cards.cache_clear()


@lru_cache(maxsize=32)
def _source_cards(path_string, modified):
    from cardparse import extract
    doc, parsed = extract(path_string)
    return doc, [dict(tag=c['tag'], cite=c['cite'], body=c['body'], paras=c['_paras']) for c in parsed]


def resolve(card):
    source = card.get('source_file','')
    path = (BASE/'docs'/source).resolve()
    if not source or not path.is_relative_to((BASE/'docs').resolve()) or not path.is_file():
        raise ValueError('Original Word source is unavailable; exact formatting cannot be recovered.')
    doc,cards=source_cards(str(path),path.stat().st_mtime_ns)
    matches=[c for c in cards if norm(c['tag'])==norm(card['tag']) and norm(c['cite'])==norm(card['cite'])]
    exact=[c for c in matches if norm(' '.join(c['body']))==norm(card.get('body',''))]
    if exact: matches=exact
    if not matches:
        raise ValueError('The card could not be matched to its original Word paragraphs.')
    if len(matches)>1:
        # Match the legacy selection to distinguish multiple cuts in the same file.
        wanted=norm(card.get('read',''))
        selected=[c for c in matches if norm(' '.join(r.text for p in c['paras'][2:] for r in p.runs
                    if r.underline or r.font.highlight_color))==wanted]
        if selected: matches=selected
        if len({tuple((p.text, tuple((text, tuple(sorted(a.items())), size)
                          for text,a,size in pieces(p, 'body'))) for p in c['paras']) for c in matches})>1:
            chosen = source_occurrence(card, path, cards, matches)
            if chosen is None:
                raise ValueError('Multiple source cuts match this card; their formatting differs. Review the original source.')
            matches = [chosen]
    return doc,matches[0]['paras']


def source_occurrence(card, path, all_source_cards, candidates):
    """Recover duplicate occurrence from original heading metadata and ingest order.

    This is only needed when text/read markers match but bold or colors differ.
    Never select an arbitrary differently formatted occurrence.
    """
    if not card.get('id') or not all(k in card for k in ('speech','arg_heading','block_heading')):
        return None
    from cardgrab import parse, DB, ANSWER
    import sqlite3
    def same(c):
        body=c['body'] if isinstance(c['body'],str) else ' '.join(c['body'])
        return norm(c['tag'])==norm(card['tag']) and norm(c['cite'])==norm(card['cite']) and norm(body)==norm(card['body'])
    source=[c for c in all_source_cards if same(c)]
    parsed=[c for c in parse(path) if same(c)]
    if len(source)!=len(parsed):return None
    eligible=[]
    for src,metadata in zip(source,parsed):
        speech = metadata['speech'] or (
            'A/2 / AT heading' if ANSWER.match(metadata['arg']) or ANSWER.match(metadata['block'])
            else 'Unlabeled section'
        )
        # Older indexes used the answer label for all unlabeled sections.
        if not metadata['speech'] and card['speech'] == 'A/2 / AT heading':
            speech = card['speech']
        exact_text = (src['tag']==card['tag'] and src['cite']==card['cite']
                      and ' '.join(src['body'])==card['body'])
        if exact_text and (speech,metadata['arg'],metadata['block']) == (card['speech'],card['arg_heading'],card['block_heading']):
            eligible.append(src)
    con=sqlite3.connect(DB)
    try:
        ids=[r[0] for r in con.execute(
            'SELECT rowid FROM cards WHERE source_file=? AND tag=? AND cite=? AND body=? '
            'AND speech=? AND arg_heading=? AND block_heading=? ORDER BY rowid',
            tuple(card[k] for k in ('source_file','tag','cite','body','speech','arg_heading','block_heading')))]
    finally:con.close()
    if len(ids)!=len(eligible) or int(card['id']) not in ids:return None
    chosen=eligible[ids.index(int(card['id']))]
    return chosen if any(chosen is candidate for candidate in candidates) else None


def style_chain(style):
    seen=set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id);yield style;style=style.base_style


def property_value(run, paragraph, name):
    value=getattr(run.font,name)
    if value is not None: return value
    for style in list(style_chain(run.style))+list(style_chain(paragraph.style)):
        value=getattr(style.font,name)
        if value is not None:return value
    return None


def run_property(run, paragraph, tag):
    elements=[run._r.rPr]
    elements += [s.element.rPr for s in list(style_chain(run.style))+list(style_chain(paragraph.style))]
    for element in elements:
        if element is not None:
            found=element.find(qn('w:'+tag))
            if found is not None:return found
    # Word document defaults can also supply text formatting.
    defaults=paragraph.part.document.styles.element.find(qn('w:docDefaults'))
    if defaults is not None:
        found=defaults.find('.//'+qn('w:'+tag))
        if found is not None:return found
    return None


def attributes(run, paragraph):
    b=property_value(run,paragraph,'bold')
    if b is None:
        el=run_property(run,paragraph,'b');b=el is not None and el.get(qn('w:val'),'1') not in ('0','false','off')
    u=run_property(run,paragraph,'u')
    underline=u.get(qn('w:val'),'single') if u is not None else None
    if underline in ('none','0','false'):underline=None
    highlight=run_property(run,paragraph,'highlight')
    highlight=HIGHLIGHTS.get(highlight.get(qn('w:val'))) if highlight is not None else None
    shading=run_property(run,paragraph,'shd')
    shade=shading.get(qn('w:fill')) if shading is not None else None
    if not shade or not re.fullmatch('[0-9A-Fa-f]{6}',shade):shade=None
    color=run_property(run,paragraph,'color')
    color=color.get(qn('w:val')) if color is not None else None
    if not color or not re.fullmatch('[0-9A-Fa-f]{6}',color):color=None
    parent=run._r.getparent();link=None
    if parent is not None and parent.tag==qn('w:hyperlink'):
        relationship=paragraph.part.rels.get(parent.get(qn('r:id')))
        if relationship is not None and relationship.is_external:link=relationship.target_ref
    return {'bold':bool(b),'underline':underline,'highlight':highlight,'shading':shade,'color':color,'link':link,
            'italic':bool(property_value(run,paragraph,'italic')),
            'strike':bool(property_value(run,paragraph,'strike')),
            'font':property_value(run,paragraph,'name') or 'Arial'}


def body_size(a):
    if a['highlight'] or a['shading']:return 11
    if a['bold']:return 9.5
    if a['underline']:return 11
    return 8


def citation_lead(text):
    # Only the author/year prefix is large; credentials, date, URL and title stay 8 pt.
    match=re.match(r"^.{1,100}?(?:\b(?:19|20)\d{2}\b|(?<!\d)['’]?\d{2}(?!\d))",text)
    return match.end() if match else 0


def pieces(paragraph, role):
    lead=citation_lead(paragraph.text) if role=='cite' else 0
    offset=0
    for element in paragraph._p.iter(qn('w:r')):
        run=Run(element,paragraph);text=run.text;a=attributes(run,paragraph)
        boundaries=[0,len(text)]
        if role=='cite' and offset<lead<offset+len(text):boundaries.insert(1,lead-offset)
        for start,end in zip(boundaries,boundaries[1:]):
            size=14 if role=='tag' or (role=='cite' and offset+start<lead) else (8 if role=='cite' else body_size(a))
            yield text[start:end],a,size
        offset+=len(text)


def html_paragraph(p,role):
    spans=[]
    for text,a,size in pieces(p,role):
        css=[f'font-size:{size}pt','font-family:'+a['font'].replace(';','').replace('"',''),
             'font-weight:'+('700' if a['bold'] or role=='tag' else '400')]
        if a['italic']:css.append('font-style:italic')
        decorations=[]
        if a['underline']:decorations.append('underline')
        if a['strike']:decorations.append('line-through')
        if decorations:css.append('text-decoration-line:'+' '.join(decorations))
        if a['underline'] in ('double','dotted','dash','wave'):
            css.append('text-decoration-style:'+{'dash':'dashed','wave':'wavy'}.get(a['underline'],a['underline']))
        background=a['highlight'] or a['shading']
        if background:css.append('background-color:#'+background)
        if a['color']:css.append('color:#'+a['color'])
        content=escape(text).replace('\n','<br>').replace('\t','&#9;')
        spans.append('<span style="'+escape(';'.join(css),quote=True)+'">'+content+'</span>')
    return '<p style="margin:0 0 6pt;white-space:pre-wrap;line-height:1.15">'+''.join(spans)+'</p>'


def formatted(card):
    doc,paras=resolve(card)
    body=''.join(html_paragraph(p,'body') for p in paras[2:])
    tag,cite=html_paragraph(paras[0],'tag'),html_paragraph(paras[1],'cite')
    html=tag+cite+body
    return {'body_html':body,'tag_html':tag,'cite_html':cite,
            'clipboard_html':'<!doctype html><html><meta charset="utf-8"><body>'+html+'</body></html>',
            'plain_text':'\n'.join(p.text for p in paras),'formatting_source':'Original Word document'}


def docx_bytes(card):
    source_doc,paras=resolve(card)
    # Reopen the original package to retain its styles, hyperlinks, theme and media relationships.
    path=(BASE/'docs'/card['source_file']).resolve()
    doc=Document(path)
    body=doc._element.body
    for child in list(body):
        if child.tag!=qn('w:sectPr'):body.remove(child)
    for index,original in enumerate(paras):
        p_element=deepcopy(original._p)
        sect=body.find(qn('w:sectPr'))
        if sect is not None:sect.addprevious(p_element)
        else:body.append(p_element)
        from docx.text.paragraph import Paragraph
        p=Paragraph(p_element,doc._body)
        role='tag' if index==0 else 'cite' if index==1 else 'body'
        lead=citation_lead(p.text) if role=='cite' else 0
        offset=0
        for element in list(p._p.iter(qn('w:r'))):
            run=Run(element,p);text=run.text;a=attributes(run,p)
            cut=lead-offset
            if role=='cite' and 0<cut<len(text):
                second=deepcopy(element);element.addnext(second)
                run.text=text[:cut];Run(second,p).text=text[cut:]
                run.font.size=Pt(14);Run(second,p).font.size=Pt(8)
            else:
                size=14 if role=='tag' or (role=='cite' and offset<lead) else 8 if role=='cite' else body_size(a)
                run.font.size=Pt(size)
            if role=='tag':run.bold=True
            offset+=len(text)
        p.paragraph_format.space_after=Pt(6)
        p.paragraph_format.keep_with_next= index<2
        p.paragraph_format.page_break_before=False
    for section in doc.sections:
        section.page_width=Inches(8.5);section.page_height=Inches(11)
        section.top_margin=section.bottom_margin=section.left_margin=section.right_margin=Inches(.7)
        # Tournament headers are not part of the extracted card.
        for part in (section.header,section.footer,section.first_page_header,section.first_page_footer,
                     section.even_page_header,section.even_page_footer):
            for child in list(part._element):part._element.remove(child)
    buffer=io.BytesIO();doc.save(buffer);return buffer.getvalue()
