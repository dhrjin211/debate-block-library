"""Cards you moved by hand ("Move to…" on a card), kept in corrections.json.

A correction is tied to the card's evidence text, not to a row number, so it survives
updates, re-reads and the same card turning up in another team's file. Corrections
also teach the learned sorter (learn.py), so similar cards follow on their own.
"""
import hashlib
import json
import threading
from pathlib import Path

BASE = Path(__file__).resolve().parent
PATH = BASE / 'corrections.json'
REASON = 'Moved by you'
_LOCK = threading.Lock()


def key(evidence, cite=''):
    """Stable id from the card's normalized evidence (plus cite for very short cards)."""
    text = evidence if len(evidence) >= 80 else evidence + '|' + cite
    return hashlib.sha1(text.encode()).hexdigest()[:20]


def load():
    try:
        data = json.loads(PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def save(card_key, side, arg, tag='', new_tag=None):
    """Save a move (side + arg) and/or a hand-written tag (new_tag) for one card."""
    arg = ' '.join((arg or '').split())[:60]
    new_tag = ' '.join((new_tag or '').split())[:300]
    if not arg and not new_tag:
        raise ValueError('Pick or type a category.')
    if arg and side not in ('AT AFF', 'AT NEG', 'Other / unclear'):
        raise ValueError('Unknown side.')
    with _LOCK:
        data = load()
        entry = dict(data.get(card_key) or {}, tag=(tag or '')[:200])
        if arg:
            entry.update(side=side, arg=arg)
        if new_tag:
            entry['new_tag'] = new_tag
        data[card_key] = entry
        temporary = PATH.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, indent=1))
        temporary.replace(PATH)
    return data[card_key]


def apply(rows, key_of):
    """Put every copy of a corrected card where the user moved it. Returns how many."""
    data = load()
    if not data:
        return 0
    moved = 0
    for row in rows:
        fix = data.get(key_of(row))
        if fix and fix.get('new_tag'):
            row['custom_tag'] = fix['new_tag']
        if fix and fix.get('arg'):
            row.update(side=fix['side'], arg=fix['arg'], topic_reason=REASON,
                       needs_review=fix['arg'] == 'Unsorted')
            moved += 1
    return moved
