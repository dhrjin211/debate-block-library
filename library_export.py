"""Export a current, deduplicated block library for Word or Google Docs.

Each request uses the current dashboard records, and evidence comes exclusively
from the original Word paragraphs (text, highlighting, underlining, images and
equations).

Speed: every card's formatting is read from its Word file once, several files at
a time, and remembered (keyed by the file's size and modification time). The
next export only reads cards from new or changed files.
"""
import base64
import hashlib
import io
import json
import re
import sqlite3
import time
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from docx import Document
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.enum.text import WD_ALIGN_PARAGRAPH

from cardformat import HIGHLIGHTS, pieces, prose_equation, resolve

BASE = Path(__file__).resolve().parent
SIDE_ORDER = {'AT AFF': 0, 'AT NEG': 1}
VML_IMAGE = '{urn:schemas-microsoft-com:vml}imagedata'
MEDIA_TAGS = (qn('w:drawing'), qn('w:pict'), qn('m:oMath'))
# Fields a worker needs to find a card's original paragraphs (see cardformat.resolve).
CARD_FIELDS = ('id', 'source_file', 'tag', 'cite', 'body', 'read', 'speech', 'arg_heading', 'block_heading')


# ---------- reading formatting from the original files ----------

def _paragraph_data(p, role):
    """Plain-data copy of one source paragraph: formatted text pieces, plus any
    pictures (with their image bytes) and equations."""
    media = []
    if any(e.tag in MEDIA_TAGS for e in p._p.iter()):
        from lxml import etree
        for element in p._p.iter(qn('w:drawing'), qn('w:pict')):
            images = {}
            for blip in element.iter(qn('a:blip'), VML_IMAGE):
                key = qn('r:embed') if blip.tag == qn('a:blip') else qn('r:id')
                rel_id = blip.get(key)
                part = p.part.related_parts.get(rel_id) if rel_id else None
                if part is not None:
                    images[rel_id] = [part.content_type, part.partname.ext or 'bin',
                                      base64.b64encode(part.blob).decode()]
            media.append(dict(kind='picture', xml=etree.tostring(element).decode(), images=images))
        for element in p._p.iter(qn('m:oMathPara'), qn('m:oMath')):
            if element.tag == qn('m:oMath') and element.getparent().tag == qn('m:oMathPara'):
                continue  # copied with its equation paragraph
            if prose_equation(''.join(t.text or '' for t in element.iter(qn('m:t')))):
                continue  # pasted prose, already part of the text (see cardformat.pieces)
            media.append(dict(kind='math', xml=etree.tostring(element).decode()))
    return dict(text=p.text, pieces=[[t, a, s] for t, a, s in pieces(p, role)], media=media)


def _card_data(card):
    _, paragraphs = resolve(card)
    return ([_paragraph_data(paragraphs[0], 'tag'), _paragraph_data(paragraphs[1], 'cite')]
            + [_paragraph_data(p, 'body') for p in paragraphs[2:]])


def _format_file(cards):
    """Worker: every requested card from ONE source file, read once."""
    out = {}
    for card in cards:
        try:
            out[_card_key(card)] = dict(paragraphs=_card_data(card))
        except Exception as error:  # reported in the export, never silently dropped
            out[_card_key(card)] = dict(error=str(error) or type(error).__name__)
    return out


def _card_key(card):
    text = '\0'.join(str(card.get(k, '')) for k in ('source_file', 'tag', 'cite', 'body', 'read'))
    return hashlib.sha1(text.encode()).hexdigest()


# Bump when _paragraph_data changes what it saves. Changes to how the document is
# written don't need a bump, so app updates keep everyone's remembered formatting.
FORMAT_VERSION = '1'


def _code_version():
    code = b''.join((BASE / name).read_bytes() for name in ('cardformat.py', 'cardparse.py'))
    return FORMAT_VERSION + '-' + hashlib.sha256(code).hexdigest()[:12]


def _file_stamp(source_file):
    try:
        stat = (BASE / 'docs' / source_file).stat()
    except (OSError, ValueError):
        return None
    return f'{stat.st_size}:{stat.st_mtime_ns}'


def _minutes(seconds):
    if seconds < 60:
        return 'under a minute'
    m = round(seconds / 60)
    return f'about {m} minute' + ('s' if m != 1 else '')


def prepare(cards, log=print, workers=None, cache_path=None):
    """Formatting for every card, reading each source file at most once (in parallel)
    and reusing remembered results for unchanged files."""
    cache_path = cache_path or BASE / 'parse-cache.db'
    version = _code_version()
    con = sqlite3.connect(cache_path)
    con.execute('CREATE TABLE IF NOT EXISTS exported (key TEXT PRIMARY KEY, data TEXT)')
    results, todo = {}, defaultdict(list)
    for card in cards:
        stamp = _file_stamp(card.get('source_file', ''))
        key = f"{version}|{stamp}|{_card_key(card)}"
        row = con.execute('SELECT data FROM exported WHERE key=?', (key,)).fetchone() if stamp else None
        if row:
            results[_card_key(card)] = json.loads(row[0])
        else:
            todo[card.get('source_file', '')].append({k: card.get(k, '') for k in CARD_FIELDS})
    total = sum(len(v) for v in todo.values())
    if results and total:
        log(f'{len(results)} cards ready from last time; formatting {total} new cards.')
    elif total:
        log(f'Formatting {total} cards from {len(todo)} files.')
    started = last = time.monotonic()
    done = 0

    def store(found, source_file):
        stamp = _file_stamp(source_file)
        for key, data in found.items():
            results[key] = data
            if stamp and 'error' not in data:
                con.execute('INSERT OR REPLACE INTO exported VALUES (?,?)',
                            (f'{version}|{stamp}|{key}', json.dumps(data)))

    def progress():
        nonlocal last
        now = time.monotonic()
        if now - last >= 2 or done == total:
            last = now
            left = (now - started) / max(done, 1) * (total - done)
            log(f'Preparing cards: {done} of {total}' + (f' · {_minutes(left)} left' if done < total else ' · done'))

    if len(todo) <= 2 or workers == 0:
        for source_file, group in todo.items():
            store(_format_file(group), source_file)
            done += len(group); progress()
    else:
        import os
        from concurrent.futures import ProcessPoolExecutor, as_completed
        workers = workers or max(1, (os.cpu_count() or 2) - 1)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_format_file, group): source_file for source_file, group in todo.items()}
            for future in as_completed(futures):
                source_file = futures[future]
                store(future.result(), source_file)
                done += len(todo[source_file]); progress()
                if done % 500 < len(todo[source_file]):
                    con.commit()
    con.commit()
    con.close()
    return results


# ---------- writing the export ----------

_drawing_ids = [10000]  # unique ids for copied pictures within one export


def _add_media(doc, media):
    from docx.opc.part import Part
    for item in media:
        holder = doc.add_paragraph(style='Normal')
        element = parse_xml(item['xml'])
        if item['kind'] == 'math':
            holder._p.append(element)
            continue
        ok = True
        for blip in element.iter(qn('a:blip'), VML_IMAGE):
            key = qn('r:embed') if blip.tag == qn('a:blip') else qn('r:id')
            image = item['images'].get(blip.get(key))
            if not image:
                ok = False
                break
            content_type, ext, data = image
            part = Part(doc.part.package.next_partname(f'/word/media/image%d.{ext}'), content_type,
                        base64.b64decode(data), doc.part.package)
            blip.set(key, doc.part.relate_to(part, RT.IMAGE))
        for prop in element.iter(qn('wp:docPr')):
            _drawing_ids[0] += 1
            prop.set('id', str(_drawing_ids[0]))
        if ok:
            holder.add_run()._r.append(element)
        else:
            holder.add_run('[image from the original card could not be copied]').italic = True


HIGHLIGHT_NAMES = {v: k for k, v in HIGHLIGHTS.items()}
_XML_UNSAFE = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f]')
_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')


def _attr(value):
    return (value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def _run_xml(text, a, size, bold):
    """One formatted run as WordprocessingML (properties in the order Word expects)."""
    props = []
    if a.get('font'):
        font = _attr(a['font'])
        props.append(f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>')
    if bold: props.append('<w:b/>')
    if a.get('italic'): props.append('<w:i/>')
    if a.get('strike'): props.append('<w:strike/>')
    if a.get('color'): props.append(f'<w:color w:val="{_attr(a["color"])}"/>')
    half_points = round(float(size) * 2)
    props.append(f'<w:sz w:val="{half_points}"/><w:szCs w:val="{half_points}"/>')
    if a.get('highlight') in HIGHLIGHT_NAMES:
        props.append(f'<w:highlight w:val="{HIGHLIGHT_NAMES[a["highlight"]]}"/>')
    if a.get('underline'): props.append(f'<w:u w:val="{_attr(a["underline"])}"/>')
    if a.get('shading'): props.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{_attr(a["shading"])}"/>')
    content = []
    # Tabs and line breaks become Word's own elements, like python-docx's add_run.
    for i, line in enumerate(_XML_UNSAFE.sub('', text).split('\n')):
        if i: content.append('<w:br/>')
        for j, part in enumerate(line.split('\t')):
            if j: content.append('<w:tab/>')
            if part:
                escaped = part.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                content.append(f'<w:t xml:space="preserve">{escaped}</w:t>')
    return f'<w:r><w:rPr>{"".join(props)}</w:rPr>{"".join(content)}</w:r>'


def _source_paragraph(doc, data, role, response_number=None):
    # Formatting was resolved from the source styles already, so no style IDs from
    # different teams' files can collide in the shared document.
    skip = 0
    runs = []
    if role == 'tag' and response_number is not None:
        # Replace only the old list number, preserving the tag's actual wording
        # and run formatting, including when its number spans several runs.
        match = re.match(r'^\s*(?:\[\d+\]|\d+\s*[\]\).])\s*', data['text'])
        skip = match.end() if match else 0
        runs.append(_run_xml(f'{response_number}] ', {}, 14, True))
    for text, a, size in data['pieces']:
        if skip:
            removed = min(skip, len(text)); text = text[removed:]; skip -= removed
        if not text: continue
        run = _run_xml(text, a, size, bool(a.get('bold')) or role == 'tag')
        if a.get('link'):
            rel_id = doc.part.relate_to(a['link'], RT.HYPERLINK, is_external=True)
            run = f'<w:hyperlink r:id="{rel_id}">{run}</w:hyperlink>'
        runs.append(run)
    # Build the whole paragraph in one parse instead of one API call per setting:
    # Heading 3 for tags, 6 pt after, tag and cite kept with the next paragraph.
    props = ('<w:pStyle w:val="Heading3"/>' if role == 'tag' else '') + \
            ('<w:keepNext/>' if role in ('tag', 'cite') else '') + \
            '<w:keepLines w:val="0"/><w:spacing w:after="120"/>'
    p = parse_xml(f'<w:p {_NS}><w:pPr>{props}</w:pPr>{"".join(runs)}</w:p>')
    body = doc.element.body
    if body.sectPr is not None:
        body.sectPr.addprevious(p)
    else:
        body.append(p)
    from docx.text.paragraph import Paragraph
    p = Paragraph(p, doc._body)
    if data.get('media'):
        _add_media(doc, data['media'])
    return p


def library_docx(cards, topic, source_filter='', log=lambda line: None, workers=None, cache_path=None):
    if not cards: raise ValueError('There are no rebuttal cards to export.')
    formatted = prepare(cards, log, workers, cache_path)
    log('Writing the document…')
    writing_started = last_note = time.monotonic()
    written = 0
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
    from dashboard import RESPONSE_ORDER, _sort_text
    previous_side = None
    problems = []
    for side, arg in sorted(groups, key=lambda x: (SIDE_ORDER.get(x[0], 99), x[0], x[1] == 'Unsorted', x[1].casefold())):
        if side != previous_side:
            h = doc.add_paragraph(side, 'Title')
            h.alignment = WD_ALIGN_PARAGRAPH.CENTER
            h.paragraph_format.page_break_before = previous_side is not None
            doc.add_paragraph(topic)
            count = sum(len(group) for (s, _), group in groups.items() if s == side)
            doc.add_paragraph(f'{count} responses • Exported {stamp}')
            if source_filter: doc.add_paragraph('School or team filter: ' + source_filter)
        heading = arg if re.match(r'^AT\b', arg, re.I) else 'AT ' + arg
        doc.add_heading(heading, 1)
        # Same order as the dashboard sections: response type, most-read, then tag without source numbering.
        ordered = sorted(groups[(side, arg)], key=lambda c: (RESPONSE_ORDER.get(c['block'], 99), c['block'].casefold(),
                         -len(c.get('teams') or ()), _sort_text(c['tag']), c['source_file']))
        number = 0
        for card in ordered:
            written += 1
            now = time.monotonic()
            if now - last_note >= 2:
                last_note = now
                left = (now - writing_started) / written * (len(cards) - written)
                log(f'Writing the document: {written} of {len(cards)} cards · {_minutes(left)} left')
            data = formatted.get(_card_key(card), {'error': 'not prepared'})
            if 'error' in data:
                problems.append((side, heading, card, data['error']))
                continue
            number += 1
            paragraphs = data['paragraphs']
            tag = paragraphs[0]
            if card.get('shared_tag'):
                # The clearest tag among the teams who read this card, or the user's own.
                tag = dict(text=card['shared_tag'], pieces=[[card['shared_tag'], {}, 14]], media=[])
            _source_paragraph(doc, tag, 'tag', number)
            _source_paragraph(doc, paragraphs[1], 'cite')
            for paragraph in paragraphs[2:]: _source_paragraph(doc, paragraph, 'body')
        previous_side = side
    if problems:
        # Never silently drop a card: list anything that couldn't be copied exactly.
        h = doc.add_paragraph('Cards that could not be exported', 'Title')
        h.paragraph_format.page_break_before = True
        doc.add_paragraph(f'{len(problems)} card(s) could not be matched to their original Word '
                          'formatting, usually because the source file changed or was deleted. '
                          'Run Update blocks, then export again.')
        for side, heading, card, error in problems:
            doc.add_paragraph(f'{side} · {heading} · {card["tag"]} ({card.get("source_file", "")}): {error}')
        log(f'{len(problems)} card(s) could not be exported; they are listed at the end of the document.')
    buffer = io.BytesIO(); doc.save(buffer)
    return buffer.getvalue()


if __name__ == '__main__':
    import argparse
    import dashboard
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='', help='Optional school or team filter')
    parser.add_argument('--out', default='Rebuttal-Blocks.docx')
    parser.add_argument('--topic', default=__import__('settings').load()['topic'])
    args = parser.parse_args()
    data = library_docx(dashboard.cards_from_source(args.source), args.topic, args.source, log=print)
    Path(args.out).write_bytes(data)
    print(args.out)
