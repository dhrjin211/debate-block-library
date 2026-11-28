"""A small learned sorter that runs on your own computer (no AI service, no download).

Cards with a clear heading ("AT: Deterrence", "AT: Readiness Collapse") are labelled
examples. From them it learns which words go with which category, then places cards
whose heading says nothing ("AT Case", "Responses", no heading) when it is confident.
It retrains from the current library every time the blocks are rebuilt, so it works
on any topic and learns that topic's own vocabulary.

Two signals: a multinomial naive Bayes model over the card's tag (counted most),
underlined text and evidence; and the citation (other teams filing the same article
under one category). It only moves cards out of Unsorted, never overrides a heading.
"""
import math
import re
from collections import Counter, defaultdict

from organize import REVIEW

# Categories taken from a person's heading are the training labels.
LABEL_REASONS = {'Recognized answer heading', 'Answer heading from the source', 'Short source heading',
                 'Moved by you'}
CORRECTION_WEIGHT = 3   # a card you moved by hand teaches as much as three headed cards
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
                # Each word counts at most 3 times per card, so one long card doesn't
                # drown out the rest. Hand corrections count extra.
                weight = CORRECTION_WEIGHT if row.get('topic_reason') == 'Moved by you' else 1
                counts.update({w: weight * min(c, 3) for w, c in features(row).items()})
            self.word_counts[name] = counts
            self.totals[name] = sum(counts.values())
            vocabulary.update(counts)
            self.prior[name] = math.log(len(groups[name]) / examples)
        self.vocabulary = len(vocabulary) + 1
        # Computed once so scoring is fast: every category starts from its "word never
        # seen" score, and each word adds a bonus only for the categories it appears in.
        self.unseen = {}
        self.bonus = defaultdict(list)
        for name in self.labels:
            total = self.totals[name] + self.vocabulary
            self.unseen[name] = math.log(1 / total)
            for w, c in self.word_counts[name].items():
                self.bonus[w].append((name, math.log(c + 1)))

    def ready(self):
        return len(self.labels) >= 2

    def scores(self, card):
        found = {w: min(c, 3) for w, c in features(card).items()}
        words = sum(found.values())
        result = {name: self.prior[name] + words * self.unseen[name] for name in self.labels}
        for w, c in found.items():
            for name, bonus in self.bonus.get(w, ()):
                result[name] += c * bonus
        return result

    def predict(self, card):
        """(category, lead): the best category and how clearly it won, or (None, 0)."""
        if not self.ready():
            return None, 0.0
        scores = self.scores(card)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        words = sum(min(c, 3) for c in features(card).values()) or 1
        return ranked[0][0], (ranked[0][1] - ranked[1][1]) / math.sqrt(words)


# ---------- citations ----------
# The same article (author + year, or link) is often cut by many teams. If at least
# two teams filed it under the same category, and most uses agree, a vague card
# citing it belongs there too. Tested on the same 15,000 cards: about 91% right.
URL = re.compile(r'https?://[^\s<>\]\)]+')
AUTHOR_YEAR = re.compile(r"^\W*([A-Z][A-Za-z'’\-]+)(?:\s+(?:et\s+al\.?|and\s+[A-Z][\w'’\-]+|&\s*[A-Z][\w'’\-]+))?"
                         r"[\s,]*['’]?(\d{2}|\d{4})\b")
CITE_TEAMS = 2
CITE_SHARE = 0.75
CITE_REASON = 'Other teams filed this article here'


def citation_key(cite):
    """'u:site/path' for a link, else 'a:smith24' for author + year, else None."""
    cite = cite or ''
    link = URL.search(cite)
    if link:
        return 'u:' + re.sub(r'^https?://(www\.)?', '', link.group().rstrip('.,;').lower()).split('?')[0]
    found = AUTHOR_YEAR.match(cite)
    return 'a:' + found.group(1).lower() + found.group(2)[-2:] if found else None


def citation_votes(rows):
    """citation key -> Counter of categories (from cards with a person's label), and teams per category."""
    votes, teams = defaultdict(Counter), defaultdict(lambda: defaultdict(set))
    for row in rows:
        if row.get('topic_reason') in LABEL_REASONS and row.get('arg') not in (None, '', REVIEW):
            key = citation_key(row.get('cite'))
            if key:
                votes[key][row['arg']] += 1
                teams[key][row['arg']].add(row.get('source') or row.get('source_file'))
    return votes, teams


def citation_guess(row, votes, teams):
    key = citation_key(row.get('cite'))
    if not key or key not in votes:
        return None
    name, count = votes[key].most_common(1)[0]
    if len(teams[key][name]) >= CITE_TEAMS and count / sum(votes[key].values()) >= CITE_SHARE:
        return name
    return None


def learned_sort(rows, log=lambda line: None):
    """Move Unsorted cards when the word match or the citation match is confident and
    the two don't disagree. Returns how many moved."""
    unsorted = [row for row in rows if row.get('arg') == REVIEW]
    if not unsorted:
        return 0
    sorter = Sorter(rows)
    votes, teams = citation_votes(rows)
    moved = 0
    for row in unsorted:
        by_words, lead = sorter.predict(row)
        by_words = by_words if lead >= MIN_LEAD else None
        by_cite = citation_guess(row, votes, teams)
        if by_words and by_cite and by_words != by_cite:
            continue  # the two signals disagree: leave it for a person
        if by_cite:
            row.update(arg=by_cite, topic_reason=CITE_REASON, needs_review=False)
        elif by_words:
            row.update(arg=by_words, topic_reason=LEARNED_REASON, needs_review=False)
        else:
            continue
        moved += 1
    if moved:
        log(f'Sorted {moved} of {len(unsorted)} unsorted cards by matching them to similar cards and citations.')
    return moved
