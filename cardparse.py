"""Shared source boundaries for indexing and exact-format exports.

Detect citation-backed tags in ordinary Word styles as well as Verbatim tags.
Keep actual paragraph objects so exported evidence never comes from a summary.
"""
import re
from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from organize import looks_like_citation

SPEECH = re.compile(r'^(?:[123](?:AC|NC|AR|NR)|constructive|case|rebuttals?|rebs?|summary|final\s?focus|front\s?lines?|blocks?)\b', re.I)
ANSWER = re.compile(r'^(?:A\s*/\s*[2T]|A2)\b|^AT(?:\s*[:—–-]|\s+[A-Z])')


def source_paragraphs(doc):
    return [Paragraph(p, doc._body) for p in doc._element.body.iter(qn('w:p')) if ''.join(p.itertext()).strip()]


def heading_level(p):
    m = re.fullmatch(r'Heading ([1-9])', p.style.name)
    return int(m[1]) if m else None


def is_section(text):
    numeric = re.match(r'^[123](?:AC|NC|AR|NR)\b',text,re.I)
    named = re.fullmatch(r'(?:constructive|case|rebuttals?|rebs?|summary|final\s?focus|front\s?lines?|blocks?)(?:\s*[:—–-].*)?',text,re.I)
    return len(text) <= 180 and len(text.split()) <= 22 and not looks_like_citation(text) and bool(numeric or named or ANSWER.match(text))


def plausible_tag(p):
    text = p.text.strip()
    if not text or len(text) > 700 or len(text.split()) > 100 or looks_like_citation(text) or is_section(text):
        return False
    # Citation proximity supplies the evidence boundary. Require a short claim
    # or heading/typographic support for longer tags, rather than swallowing
    # the preceding article paragraph as a newly invented claim.
    if len(text) <= 240 or heading_level(p):
        return True
    runs = [r for r in p.runs if r.text.strip()]
    bold = sum(len(r.text) for r in runs if r.bold or (r.style is not None and r.style.font.bold))
    return bold >= len(text) * .6


def extract(path):
    doc = Document(path)
    paras = source_paragraphs(doc)
    starts = set()
    citations = set()
    for i, p in enumerate(paras):
        if looks_like_citation(p.text):
            citations.add(i)
            if i and plausible_tag(paras[i-1]): starts.add(i-1)
    # Existing Verbatim tags with short citations lacking URLs remain supported.
    for i, p in enumerate(paras):
        next_text=paras[i+1].text.strip() if i+1<len(paras) else ''
        short_cite = len(next_text)<500 and re.search(r"(?:\b(?:19|20)\d{2}\b|['’]\d{2}\b)",next_text[:100])
        if heading_level(p) == 4 and i not in citations and plausible_tag(p) and (i+1 in citations or short_cite):
            starts.add(i)
    cards=[]; current=None; speech=''; speech_level=0; headings={}
    for i,p in enumerate(paras):
        text=p.text.strip(); level=heading_level(p)
        if i in starts:
            current={'tag':text,'cite':'','body':[],'read':[],'marked':[],
                     'speech':speech,'arg':next(iter(headings.values()),''),
                     'block':list(headings.values())[-1] if len(headings)>1 else '',
                     'headings':list(headings.values()), '_paras':[p]}
            cards.append(current)
            continue
        if current is not None and not current['cite']:
            current['cite']=text; current['_paras'].append(p)
            continue
        section = is_section(text)
        prose_heading = level and (len(text)>140 or len(text.split())>18)
        if section or (level and level < 4 and not prose_heading and i not in citations):
            current=None
            level=level or (1 if SPEECH.match(text) else 2)
            if SPEECH.match(text):
                speech=text; speech_level=level; headings={}
                target=re.split(r'(?:---|[–—:]|\s+-\s+)\s*',text,maxsplit=1)
                if len(target)>1 and ANSWER.match(target[1]):headings[level]=target[1]
            else:
                if speech_level and level<=speech_level and not ANSWER.match(text):
                    speech='';speech_level=0
                headings={k:v for k,v in headings.items() if k<level}
                headings[level]=text
            continue
        if current is not None:
            current['body'].append(text);current['_paras'].append(p)
            runs=[r for r in p.runs]
            current['read'].extend(r.text for r in runs if r.underline or r.font.highlight_color)
            current['marked'].append(''.join('⟦'+r.text+'⟧' if r.underline or r.font.highlight_color else r.text for r in runs).replace('⟧⟦',''))
    return doc,[c for c in cards if c['body']]
