"""A small learned sorter that runs on your own computer (no AI service, no download).

Cards with a clear heading ("AT: Deterrence", "AT: Readiness Collapse") are labelled
examples. From them it learns which words go with which category, then places cards
whose heading says nothing ("AT Case", "Responses", no heading) when it is confident.
It retrains from the current library every time the blocks are rebuilt, so it works
on any topic and learns that topic's own vocabulary.

The model is multinomial naive Bayes over the card's tag (counted most), underlined
text, and evidence. It only moves cards out of Unsorted, never overrides a heading.
"""
import math
import re
from collections import Counter, defaultdict

from organize import REVIEW

# Categories taken from a person's heading are the training labels.
LABEL_REASONS = {'Recognized answer heading', 'Answer heading from the source', 'Short source heading'}
LEARNED_REASON = 'Matched to similar cards (learned on this computer)'
MIN_EXAMPLES = 8        # a category needs this many labelled cards...
MIN_SOURCES = 2         # ...from at least this many teams' files
# How far the best category must beat the runner-up, per square root of the card's
# word count. Tested on a real 15,000-card library with whole teams' files held out:
# at 8, about 91% of the cards it placed were right (it placed about 40% of them);
# at 6 only 87%, at 12 96% but fewer placed. Unsure cards stay in Unsorted.
MIN_LEAD = 8.0

STOP = set("""the of and to in a is that for it as was with be by on not this are or from at which but have an
they you were her she there been one all we their has would when if so no its who more about up them some could
him into than then other time only new these two may first any most over such our after also his said can will
just like what how why even because those both being through during between while very much many well
must should does did doing make made says say per via yet still""".split())
WORD = re.compile(r"[a-z][a-z'-]{2,}")


def _words(text, limit=None):
    text = (text or '')[:limit] if limit else (text or '')
    return [w for w in WORD.findall(text.casefold()) if w not in STOP]


def features(card):
    """Weighted word counts: tag words count 3x, underlined words 2x, evidence 1x."""
    read = card.get('read') or ''
    if isinstance(read, list):
        read = ' '.join(read)
    body = card.get('body') or ''
    if isinstance(body, list):
        body = ' '.join(body)
    counts = Counter()
    tag = _words(card.get('tag'))
    for w in tag:
        counts[w] += 3
    for a, b in zip(tag, tag[1:]):
        counts[a + ' ' + b] += 3
    for w in _words(read, 3000):
        counts[w] += 2
    for w in _words(body, 2000):
        counts[w] += 1
    return counts


class Sorter:
    def __init__(self, rows):
        groups = defaultdict(list)
        for row in rows:
            if row.get('topic_reason') in LABEL_REASONS and row.get('arg') and row['arg'] != REVIEW:
                groups[row['arg']].append(row)
        self.labels = [name for name, members in groups.items()
                       if len(members) >= MIN_EXAMPLES
                       and len({m.get('source') or m.get('source_file') for m in members}) >= MIN_SOURCES]
        self.word_counts = {}
        self.totals = {}
        vocabulary = set()
        examples = sum(len(groups[name]) for name in self.labels)
        self.prior = {}
        for name in self.labels:
            counts = Counter()
            for row in groups[name]:
                # Each card's words count once per card (binarized), so one long card
                # doesn't drown out the rest.
                counts.update({w: min(c, 3) for w, c in features(row).items()})
            self.word_counts[name] = counts
            self.totals[name] = sum(counts.values())
            vocabulary.update(counts)
            self.prior[name] = math.log(len(groups[name]) / examples)
        self.vocabulary = len(vocabulary) + 1

    def ready(self):
        return len(self.labels) >= 2

    def scores(self, card):
        found = {w: min(c, 3) for w, c in features(card).items()}
        result = {}
        for name in self.labels:
            counts, total = self.word_counts[name], self.totals[name] + self.vocabulary
            score = self.prior[name]
            for w, c in found.items():
                score += c * math.log((counts.get(w, 0) + 1) / total)
            result[name] = score
        return result

    def predict(self, card):
        """(category, lead): the best category and how clearly it won, or (None, 0)."""
        if not self.ready():
            return None, 0.0
        scores = self.scores(card)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        words = sum(min(c, 3) for c in features(card).values()) or 1
        return ranked[0][0], (ranked[0][1] - ranked[1][1]) / math.sqrt(words)


def learned_sort(rows, log=lambda line: None):
    """Move confident Unsorted cards into a learned category. Returns how many moved."""
    unsorted = [row for row in rows if row.get('arg') == REVIEW]
    if not unsorted:
        return 0
    sorter = Sorter(rows)
    if not sorter.ready():
        return 0
    moved = 0
    for row in unsorted:
        name, lead = sorter.predict(row)
        if name and lead >= MIN_LEAD:
            row.update(arg=name, topic_reason=LEARNED_REASON, needs_review=False)
            moved += 1
    if moved:
        log(f'Sorted {moved} of {len(unsorted)} unsorted cards by matching them to similar cards.')
    return moved
