"""
dashboard.py: browse cards.db by side, topic, and response type as a block-file dashboard.

  python dashboard.py            # topic comes from Settings
then open http://localhost:8420
"""
import io
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from docx import Document
from cardgrab import DB, label, include_constructive_evidence
from pathlib import Path
from threading import Lock, RLock
import threading
import subprocess
from organize import classify, display_title, veto, heading_topic, REVIEW
from cardformat import formatted, docx_bytes

import settings
import refresh

TOPIC_OVERRIDE = ""  # set by `python dashboard.py "Topic"`; otherwise Settings decides


def topic():
    return TOPIC_OVERRIDE or settings.load()["topic"]


# One background update at a time; the page polls /api/job for progress.
JOB = {"running": False, "lines": [], "error": "", "finished": False}
JOB_LOCK = Lock()
CREDENTIALS = {}  # OpenCaselist sign-in, memory only, cleared when the app stops


def start_job(download):
    with JOB_LOCK:
        if JOB["running"]:
            raise ValueError("An update is already running.")
        JOB.update(running=True, lines=[], error="", finished=False)

    def log(line):
        line = str(line)
        # Progress lines ("Reading files: 120 of 900 · about 3 minutes left") replace each other.
        if line.startswith("Reading files:") and JOB["lines"] and JOB["lines"][-1].startswith("Reading files:"):
            JOB["lines"][-1] = line
            return
        JOB["lines"].append(line)
        del JOB["lines"][:-200]

    def run():
        try:
            if download:
                refresh.download(CREDENTIALS["email"], CREDENTIALS["password"], log)
            refresh.rebuild(log)
            log("Organizing cards for the page…")
            outline()  # warm the page now so it opens instantly afterwards
            log("Ready.")
        except Exception as error:
            if download and "did not accept" in str(error):
                CREDENTIALS.clear()
            JOB["error"] = str(error) if isinstance(error, (ValueError, RuntimeError)) else f"Update failed: {error}"
        finally:
            JOB.update(running=False, finished=True)

    threading.Thread(target=run, daemon=True).start()
OPEN, CLOSE = "\u27e6", "\u27e7"
SIDE_ORDER = {"AT AFF": 0, "AT NEG": 1, "Other / unclear": 2}
RESPONSE_ORDER = {
    "Turn": 0, "Non-uniqueness": 1, "Uniqueness": 2, "Disadvantage Link (DL)": 3,
    "Link defense": 4, "Impact mitigation": 5, "Solvency": 6, "Impact": 7, "Defense / other": 8,
}
_OUTLINE_CACHE = None
_CARD_CACHE = None
_RAW_CARD_CACHE = None
_DB_STAMP = None
_CACHE_LOCK = RLock()
# Team-filtered views, keyed by the raw-row list they were built from so a
# rebuilt library (or a test that swaps the rows) never reuses stale results.
_FILTER_CACHE = (None, {})
_FORMATTED_CACHE = OrderedDict()
_FORMATTED_LIMIT = 3000
# Same section + same source article: a cut sharing this much of its text with
# a fuller cut is the same evidence, even when teams highlighted it differently.
DUPLICATE_OVERLAP = 0.6
SHINGLE = 8
LIST_FIELDS = ("id", "tag", "cite", "speech", "source", "side", "arg", "block", "needs_review", "topic_reason")
ANSWER_MARKER = re.compile(r"\b(?:A\s*/\s*2|A2|A\s*/\s*T|AT)\b", re.I)
WARRANT_WORDS = re.compile(r"\b(?:because|means|proves?|causes?|leads? to|results? in|due to|therefore|thus|empirically|stud(?:y|ies)|data show)\b", re.I)
DIRECT_WORDS = re.compile(r"\b(?:no link|non[- ]?unique|NQ|NUQ|NU|turns?|takeout|does not|doesn't|fails? to|concedes?|solves?)\b", re.I)
WEIGHING_WORDS = re.compile(r"\b(?:outweighs?|even if|probability|magnitude|timeframe|risk|prefer|comparatively|more likely|less likely|greater|less severe)\b", re.I)


def q(sql, args=()):
    con = sqlite3.connect(DB)
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def _normalized_card_text(text):
    text = unicodedata.normalize("NFKD", text).casefold()
    return re.sub(r"\W+", "", text, flags=re.UNICODE)


def _evidence_text(card):
    return _normalized_card_text(
        (card.get("marked") or card.get("body") or "").replace(OPEN, "").replace(CLOSE, "")
    )


def _preferred_card(rows):
    return max(rows, key=lambda row: (
        len(row.get("cite", "")),
        row.get("marked", "").count(OPEN),
        len(row.get("body", "")),
    ))


def _merged_cards(rows):
    """Each piece of evidence appears once, under the argument most teams used it for."""
    exact = {}
    for row in rows:
        # Full evidence identity must not depend on the tag, side, or response-type spelling.
        evidence = _evidence_text(row)
        key = (evidence,)
        if len(evidence) < 80:
            key += (_normalized_card_text(row.get("cite", "")),)
        if not evidence:
            key += (_normalized_card_text(row.get("tag", "")),)
        exact.setdefault(key, []).append(row)
    results = []
    for members in exact.values():
        targets = {}
        for member in members:
            topic = member.get("arg") or "Other / unclear"
            targets.setdefault((member.get("side", "Other / unclear"), topic.casefold()), []).append(member)
        # One placement only: the most common one, preferring a real argument and
        # a known side over Unsorted / unclear.
        best = max(targets, key=lambda t: (
            t[1] not in ("other / unclear", REVIEW.casefold()), t[0] != "Other / unclear",
            len(targets[t]), t))
        for placements in (targets[best],):
            representative = dict(_preferred_card(placements))
            kinds = {}
            for member in placements:
                kind = member.get("block") or "Defense / other"
                kinds[kind] = kinds.get(kind, 0) + 1
            representative["block"] = min(kinds, key=lambda k: (
                k == "Defense / other", -kinds[k], RESPONSE_ORDER.get(k, 99), k))
            teams = set()
            for member in members:
                existing = member.get("teams")
                if isinstance(existing, set):
                    teams.update(existing)
                elif member.get("source"):
                    teams.add(member["source"])
            representative["teams"] = teams
            results.append(representative)
    # Several teams often cut the same article for the same argument, each with
    # its own highlighting or a slightly wider excerpt. Within one section and one
    # source, a cut that mostly repeats a fuller cut is shown once, crediting every
    # team. Different excerpts of the same article stay separate.
    source_groups = {}
    for card in results:
        source_groups.setdefault(_source_key(card), []).append(card)
    merged = []
    for source, members in source_groups.items():
        if len(members) == 1 or not source:
            merged.extend(members)
            continue
        retained = []
        for card in sorted(members, key=lambda c: (len(_evidence_text(c)), -int(c['id'])), reverse=True):
            shingles = _shingles(card)
            fuller = next((kept for kept, kept_shingles in retained if shingles and
                           len(shingles & kept_shingles) >= DUPLICATE_OVERLAP * len(shingles)), None)
            if fuller is None:
                retained.append((card, shingles))
                continue
            fuller['teams'].update(card['teams'])
            if fuller.get('block') == 'Defense / other' and card.get('block') != 'Defense / other':
                fuller['block'] = card['block']
        merged.extend(card for card, _ in retained)
    return merged


def _source_key(card):
    urls = re.findall(r'https?://[^\s<>\]]+', card.get('cite') or '')
    if urls:
        return urls[0].rstrip('.,;)').casefold()
    return _normalized_card_text(card.get('cite') or '')[:40]


def _shingles(card):
    text = (card.get("marked") or card.get("body") or "").replace(OPEN, "").replace(CLOSE, "")
    words = re.findall(r"\w+", unicodedata.normalize("NFKD", text).casefold())
    if len(words) < SHINGLE:
        return {tuple(words)} if words else set()
    return {hash(tuple(words[i:i + SHINGLE])) for i in range(len(words) - SHINGLE + 1)}


def _all_cards():
    with _CACHE_LOCK:
        return _load_cards()


def _library_stamp():
    try:
        stat = Path(DB).stat()
    except FileNotFoundError:
        return None
    # settings.json changes which cards are eligible, so it is part of the stamp.
    try:
        changed = settings.PATH.stat().st_mtime_ns
    except FileNotFoundError:
        changed = None
    return (stat.st_ino, stat.st_mtime_ns, stat.st_size, changed)


def _load_cards():
    global _CARD_CACHE, _RAW_CARD_CACHE, _OUTLINE_CACHE, _DB_STAMP
    stamp = _library_stamp()
    if stamp != _DB_STAMP:
        _CARD_CACHE = _RAW_CARD_CACHE = _OUTLINE_CACHE = None
        with _CACHE_LOCK:
            _FORMATTED_CACHE.clear()
        _DB_STAMP = stamp
    if _CARD_CACHE is None and stamp is None:
        # No library yet: show an empty dashboard instead of creating an empty database.
        _RAW_CARD_CACHE, _CARD_CACHE = [], []
    if _CARD_CACHE is None:
        include_constructive = include_constructive_evidence()
        columns = {r["name"] for r in q("PRAGMA table_info(cards)")}
        eligible = []
        if "reason" in columns:
            # Fast path: categories were decided while the files were read.
            for row in q("SELECT rowid id, tag, cite, body, read, marked, source, source_file, speech, "
                         "arg_heading, block_heading, side, arg, block, reason FROM cards"):
                row.update(topic_reason=row.pop("reason"), needs_review=row["arg"] == REVIEW,
                           display_tag=display_title(row["tag"]))
                eligible.append(row)
            rows = []
        else:
            rows = q("SELECT rowid id, tag, cite, body, read, marked, source, source_file, speech, "
                     "arg_heading, block_heading, side, arg, block FROM cards")
        names = {}
        for row in rows:
            card = {**row, "body": [row["body"]],
                    "arg": row["arg_heading"], "block": row["block_heading"]}
            placement = label(card, names, include_constructive, row["source_file"])
            if placement:
                topic, reason, needs_review = classify(card)
                row["side"], row["arg"], row["block"] = placement
                row.update(topic_reason=reason, needs_review=needs_review, display_tag=display_title(row['tag']))
                eligible.append(row)
        _keep_shared_heading_categories(eligible)
        _RAW_CARD_CACHE = eligible
        _CARD_CACHE = _merged_cards(eligible)
    return _CARD_CACHE


HEADING_REASONS = ('Answer heading from the source', 'Short source heading')


def _keep_shared_heading_categories(rows):
    """A category made from a source heading must be used by at least two different
    teams' files; one-off headings ("Action", typos) go to Unsorted instead."""
    teams = {}
    for row in rows:
        if row.get("topic_reason") in HEADING_REASONS:
            teams.setdefault(row["arg"].casefold(), set()).add(row.get("source") or row.get("source_file"))
    for row in rows:
        if row.get("topic_reason") in HEADING_REASONS and len(teams[row["arg"].casefold()]) < 2:
            row["arg"] = REVIEW


def _source_filter_key(source):
    return re.sub(r'[^a-z0-9]', '', (source or '').casefold())


def _filtered(source):
    """Cached (cards, outline) for one team filter; outline is filled lazily."""
    global _FILTER_CACHE
    key = _source_filter_key(source)
    with _CACHE_LOCK:
        raw = _RAW_CARD_CACHE or []
        if _FILTER_CACHE[0] is not raw:
            _FILTER_CACHE = (raw, {})
        entries = _FILTER_CACHE[1]
        if key not in entries:
            # Filter original records before deduplication so exports come from
            # the selected team's actual cut, rather than another team's copy.
            cards = _merged_cards([r for r in raw if key in _source_filter_key(
                ' '.join((r.get('source', ''), r.get('source_file', ''))))])
            if len(entries) >= 64:
                entries.pop(next(iter(entries)))
            entries[key] = [cards, None]
        return entries[key]


def cards_from_source(source=''):
    all_cards = _all_cards()
    if not _source_filter_key(source):
        return all_cards
    return _filtered(source)[0]


def outline(source=''):
    global _OUTLINE_CACHE
    all_cards = cards_from_source(source)
    if not _source_filter_key(source) and _OUTLINE_CACHE is not None:
        return _OUTLINE_CACHE
    if _source_filter_key(source):
        entry = _filtered(source)
        if entry[1] is not None:
            return entry[1]
    out = {}
    for card in all_cards:
        side = card["side"]
        topic = card["arg"]
        kind = card["block"]
        topics = out.setdefault(side, {})
        groups = topics.setdefault(topic, {})
        groups[kind] = groups.get(kind, 0) + 1
    result = [
        {
            "side": side,
            "n": sum(sum(groups.values()) for groups in topics.values()),
            "topics": [
                {"arg": topic, "n": sum(groups.values()),
                 "groups": [{"group": kind, "n": n} for kind, n in groups.items()]}
                for topic, groups in sorted(
                    topics.items(), key=lambda item: (item[0] == REVIEW, -sum(item[1].values()), item[0].casefold())
                )
            ],
        }
        for side, topics in sorted(out.items(), key=lambda item: SIDE_ORDER.get(item[0], 99))
    ]
    for side in result:
        for topic in side["topics"]:
            topic["groups"].sort(key=lambda group: RESPONSE_ORDER.get(group["group"], 99))
    if _source_filter_key(source):
        _filtered(source)[1] = result
    else:
        _OUTLINE_CACHE = result
    return result


def card_identity(card):
    tag = re.sub(r"^\s*\d+[\].):\-]?\s*", "", card["tag"])
    text = (card.get("marked") or card.get("body") or "").replace(OPEN, "").replace(CLOSE, "")
    return (
        card.get("side", "Other / unclear"),
        card.get("arg", "General responses"),
        card.get("block", "Defense / other"),
        _normalized_card_text(tag),
        _normalized_card_text(text) or _normalized_card_text(card.get("cite", "")),
    )


def response_strength(card):
    """Estimate argument completeness from visible text; this is a sorting aid, not adjudication."""
    tag = card["tag"]
    body = card.get("body", "")
    cite = card.get("cite", "")
    headings = " ".join((card.get("arg_heading", ""), card.get("block_heading", "")))
    score = 0
    reasons = []

    if ANSWER_MARKER.search(headings):
        score += 12
        reasons.append("explicit AT/A/2 heading")
    if DIRECT_WORDS.search(f"{tag} {headings} {body[:1200]}"):
        score += 15
        reasons.append("direct response claim")
    warrants = len(set(word.casefold() for word in WARRANT_WORDS.findall(body)))
    if warrants:
        score += min(20, warrants * 7)
        reasons.append("warrant or causal explanation")
    if WEIGHING_WORDS.search(body):
        score += 20
        reasons.append("impact comparison or weighing")
    if re.search(r"\b(?:even if|but|however|unless|only if|otherwise|despite)\b", body, re.I):
        score += 10
        reasons.append("qualification or comparison")
    if re.search(r"\b(?:19|20)\d{2}\b", cite):
        score += 8
        reasons.append("dated citation")
    if len(re.findall(r"\w+", body)) >= 60:
        score += 8
        reasons.append("developed card text")
    return round(min(score, 93) * 100 / 93), reasons


def format_version(card):
    return hashlib.sha256('\0'.join(str(card.get(k,'')) for k in ('source_file','tag','cite','body')).encode()).hexdigest()[:20]


LEADING_NUMBER = re.compile(r"^(?:\s*(?:\d{1,2}\s*[\].):\-–—]+(?!\d)|\(\d{1,2}\)|[-–—•*>]+))+\s*")


def clean_tag(tag):
    """Drop source numbering such as '2]', '4.' or '--' that only made sense in the original file."""
    cleaned = LEADING_NUMBER.sub("", tag or "").strip()
    return cleaned or (tag or "").strip()


def _sort_text(tag):
    return re.sub(r"^\W+", "", clean_tag(tag).casefold())


def preview_text(card, limit=280):
    text = re.sub(r"\s+", " ", (card.get("marked") or card.get("body") or "")
                  .replace(OPEN, "").replace(CLOSE, "")).strip()
    if len(text) <= limit:
        return text
    end = text.rfind(" ", 0, limit)
    return text[:end if end > 0 else limit] + "…"


def _list_entry(card):
    """What the card list needs. Full text comes from /api/formatted when shown."""
    if "_list_entry" not in card:
        strength, reasons = response_strength(card)
        entry = {k: card.get(k) for k in LIST_FIELDS}
        entry.update(display_tag=display_title(clean_tag(card["tag"])), preview=preview_text(card),
                     format_version=format_version(card), strength=strength, strength_reasons=reasons)
        card["_list_entry"] = entry
    return dict(card["_list_entry"], teams=len(card["teams"]))


def pack(rows):
    """Rows are already merged. Most-read evidence first, then strongest, then tag."""
    cards = [_list_entry(card) for card in rows]
    return sorted(cards, key=lambda c: (-c["teams"], -c["strength"], _sort_text(c["tag"]), c["id"]))


def cards_for(side, arg, group, source=''):
    all_cards = cards_from_source(source)
    if not any(c['side'] == side and c['arg'] == arg for c in all_cards):
        arg = heading_topic(arg) or arg
    rows = [card for card in all_cards
            if card["side"] == side and card["arg"] == arg and card["block"] == group]
    return pack(rows)


def search(text, source=''):
    tokens = re.findall(r"\w+", text.casefold(), re.UNICODE)
    if not tokens:
        return []
    wanted = set(tokens)
    rows = [card for card in cards_from_source(source) if wanted <= _search_words(card)]
    return pack(rows)[:60]


def _search_words(card):
    if "_words" not in card:
        card["_words"] = frozenset(re.findall(r"\w+", " ".join(str(card.get(key, "")) for key in
                                   ("tag", "arg", "block", "arg_heading", "block_heading")).casefold(), re.UNICODE))
    return card["_words"]


def raw_card(card_id):
    if not Path(DB).exists():
        return None
    rows = q("SELECT rowid id, tag, cite, body, read, marked, source_file, speech, arg_heading, block_heading FROM cards WHERE rowid=?", (card_id,))
    return rows[0] if rows else None


def make_docx(card_id):
    card = raw_card(card_id)
    return docx_bytes(card) if card else None


def cached_formatted(card, version):
    """Formatted card JSON, cached per card version so each Word source is parsed rarely."""
    key = (str(card["id"]), version)
    with _CACHE_LOCK:
        if key in _FORMATTED_CACHE:
            _FORMATTED_CACHE.move_to_end(key)
            return _FORMATTED_CACHE[key]
    data = json.dumps(formatted(card)).encode()
    with _CACHE_LOCK:
        _FORMATTED_CACHE[key] = data
        while len(_FORMATTED_CACHE) > _FORMATTED_LIMIT:
            _FORMATTED_CACHE.popitem(last=False)
    return data


LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


class H(BaseHTTPRequestHandler):
    def host_allowed(self):
        # Blocks DNS-rebinding pages from reading the library through a foreign hostname.
        host = (self.headers.get("Host") or "").strip().lower()
        name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        if name in LOCAL_HOSTS:
            return True
        self.send_error(403)
        return False

    def send(self, body, ctype, extra=None, status=200):
        body = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.host_allowed():
            return
        u = urlparse(self.path)
        a = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/":
            self.send(PAGE.replace("__TOPIC__", json.dumps(topic())), "text/html; charset=utf-8")
        elif u.path == '/health':
            self.send(json.dumps({'app':'debate-blocks','status':'ok'}),'application/json')
        elif u.path == "/api/settings":
            values = settings.load()
            label, released = settings.current_topic(a.get("caselist", values["caselist"]))
            self.send(json.dumps(dict(values, signed_in=bool(CREDENTIALS), effective_since=settings.since(values),
                                      current_topic=label, release=released.isoformat())), "application/json")
        elif u.path == "/api/job":
            self.send(json.dumps(JOB), "application/json")
        elif u.path == "/api/files":
            self.send(json.dumps(refresh.months()), "application/json")
        elif u.path == "/api/outline":
            self.send(json.dumps(outline(a.get('source',''))), "application/json")
        elif u.path == "/api/cards":
            self.send(json.dumps(cards_for(a.get("side", ""), a.get("arg", ""), a.get("group", ""),a.get('source',''))),
                      "application/json")
        elif u.path == "/api/search":
            self.send(json.dumps(search(a.get("q", ""),a.get('source',''))), "application/json")
        elif u.path == '/export/library.docx':
            from library_export import library_docx
            try:
                # This always exports the whole library, independent of search
                # or the selected sidebar group.
                data = library_docx(cards_from_source(), topic())
                self.send(data, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                          {'Content-Disposition': 'attachment; filename="Rebuttal-Blocks.docx"'})
            except ValueError as error:
                self.send(json.dumps({'error': str(error)}), 'application/json', status=422)
        elif u.path.startswith("/api/formatted/"):
            card = raw_card(u.path.rsplit("/", 1)[-1])
            if not card:
                self.send_error(404)
                return
            version = format_version(card)
            if a.get('v') and a['v'] != version:
                self.send(json.dumps({'error':'The library changed. Reload the page to see the current card.'}), 'application/json', status=409)
                return
            try:
                self.send(cached_formatted(card, version), "application/json")
            except ValueError as error:
                self.send(json.dumps({"error": str(error)}), "application/json", status=422)
        elif u.path.startswith("/card/") and u.path.endswith(".docx"):
            try:
                data = make_docx(u.path[6:-5])
            except ValueError as error:
                self.send(str(error), "text/plain; charset=utf-8", status=422)
                return
            if data:
                self.send(data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                          {"Content-Disposition": f'attachment; filename="{u.path[6:]}"'})
                return
            self.send_error(404)
        else:
            self.send_error(404)

    def do_POST(self):
        if not self.host_allowed():
            return
        path = urlparse(self.path).path
        try:
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                raise ValueError("Use the app from its own page.")
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 200 * 1024 * 1024:
                raise ValueError("That file is too large.")
            body = self.rfile.read(size)
            kind = self.headers.get("Content-Type", "").split(";")[0]
            if path == "/api/upload":
                # Raw file body (not a form), so other websites can't post here.
                if kind != "application/octet-stream":
                    raise ValueError("Expected a file.")
                from urllib.parse import unquote
                name = refresh.save_upload(unquote(self.headers.get("X-Filename", "")), body)
                result = {"saved": name}
            else:
                if kind != "application/json":
                    raise ValueError("Expected JSON.")
                data = json.loads(body or b"{}")
                if path == "/api/settings":
                    before = settings.load()["include_constructive"]
                    result = settings.save(data)
                    if result["include_constructive"] != before and not JOB["running"]:
                        start_job(False)  # case cards are chosen while files are read
                        result = dict(result, rebuilding=True)
                elif path == "/api/update":
                    download = bool(data.get("download"))
                    if download:
                        if data.get("email") and data.get("password"):
                            CREDENTIALS.update(email=data["email"], password=data["password"])
                        if not CREDENTIALS:
                            raise ValueError("Enter your OpenCaselist email and password.")
                    start_job(download)
                    result = {"started": True}
                elif path == "/api/files/delete":
                    if JOB["running"]:
                        raise ValueError("Wait for the current update to finish.")
                    result = {"deleted": refresh.delete_month(data.get("origin"), data.get("month"))}
                    start_job(False)
                elif path == "/api/open-folder":
                    refresh.DOCS.mkdir(exist_ok=True)
                    opener = {"darwin": ["open"], "win32": ["explorer"]}.get(sys.platform, ["xdg-open"])
                    subprocess.Popen(opener + [str(refresh.DOCS)])
                    result = {"opened": True}
                else:
                    self.send_error(404)
                    return
            self.send(json.dumps(result), "application/json")
        except (ValueError, KeyError, TypeError) as error:
            self.send(json.dumps({"error": str(error)}), "application/json", status=422)

    def log_message(self, *args):
        pass


PAGE = r"""<!doctype html><meta charset="utf-8"><title>Debate blocks</title>
<style>
body{margin:0;background:#f3f5f8;font:16px/1.4 system-ui,sans-serif;color:#222}
.wrap{max-width:1300px;margin:0 auto;padding:24px}
.box{background:#fff;border:1px solid #dde3ea;border-radius:10px}
.top{background:#16212e;color:#fff;display:flex;align-items:center;gap:16px;padding:14px 24px;margin:-24px -24px 20px;flex-wrap:wrap}
.brand{font-weight:800;font-size:20px;letter-spacing:-.01em}.brand span{color:#5eead4}
#topic{font-size:15px;color:#cbd5e1}
.cols{display:flex;gap:28px;align-items:flex-start}
#side{width:300px;flex:none;padding:20px;position:sticky;top:12px;max-height:90vh;overflow:auto}
#side h4{margin:0 0 12px;color:#16212e;letter-spacing:.08em;font-size:12px;text-transform:uppercase}
.arg,.grp{display:flex;justify-content:space-between;padding:6px 8px;cursor:pointer;border-radius:6px}
.topic{margin-left:10px}
.grp{margin-left:14px;border-left:2px solid #dde3ea}
.grp.on{color:#0f766e;font-weight:600;border-color:#0f766e}
.arg:hover,.grp:hover{background:#e8f3f1}
.n{color:#666;font-variant-numeric:tabular-nums}
#main{flex:1;min-width:0}
#search,#source{width:100%;box-sizing:border-box;padding:12px 16px;border-radius:8px;border:1px solid #dde3ea;font-size:16px;margin-bottom:16px}
h1{font:800 30px/1.2 system-ui,sans-serif;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:#555;margin-bottom:18px}
.grid{display:grid;grid-template-columns:minmax(0,1fr);gap:22px}
.grid>div,.card{min-width:0}
.card p,.card span{overflow-wrap:anywhere}
.read{color:#555;font-size:14px;margin-bottom:6px}
.card{background:#fff;border:1px solid #dde3ea;border-left:4px solid #0f766e;box-shadow:0 1px 2px rgba(15,23,42,.06);border-radius:10px;padding:22px;display:flex;flex-direction:column}
.tag{font:700 14pt/1.25 Calibri,Arial,sans-serif;margin:0 0 14px}
.cite{color:#444;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.cite b,.lbl{color:#0f766e}
.acts{padding-top:16px;display:flex;gap:16px}
.acts a,.acts span,.show{color:#0f766e;cursor:pointer;text-decoration:none;font-weight:500}
.show{display:block;text-align:right;margin-top:8px}
.preview,.body{margin-top:12px;font-size:14px;color:#555}
.body{display:block;max-height:180px;overflow:hidden;overflow-wrap:anywhere;color:#111}
.body .source-tag,.body .full-cite{display:none}
.tag{overflow-wrap:anywhere}
.source-tag{font-size:14px;margin-bottom:12px}
.full-cite{margin-bottom:12px}
.arg span:first-child{overflow-wrap:anywhere}
.review{color:#8b5300;font-size:13px;margin-bottom:8px}
.case{display:inline-block;align-self:flex-start;background:#e8f3f1;color:#0f766e;font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px;margin-bottom:8px}
.open .preview{display:none}
.open .body{display:block;max-height:none}
.open .body .source-tag,.open .body .full-cite{display:block}
.open>.tag,.open>.cite{display:none}
.formatted .preview{display:none}
#notice{min-height:1.4em;color:#0f766e;font-size:14px;margin-bottom:8px}
.bar{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.bar button,.dlg button{padding:8px 14px;cursor:pointer;border:1px solid #bbb;border-radius:8px;background:#fff;font:inherit}
button.primary{background:#0f766e;color:#fff;border-color:#0f766e}
button:disabled{opacity:.6;cursor:wait}
dialog{border:none;border-radius:12px;padding:0;max-width:520px;width:calc(100% - 32px)}
dialog::backdrop{background:rgba(0,0,0,.35)}
.dlg{padding:20px 24px;display:flex;flex-direction:column;gap:8px}
.dlg h3{margin:0 0 4px}
.dlg label{display:flex;flex-direction:column;gap:4px;font-weight:600;font-size:14px}
.dlg label.check{flex-direction:row;align-items:center;font-weight:400}
.dlg input:not([type=checkbox]){padding:8px;border:1px solid #ccc;border-radius:8px;font:inherit;font-weight:400}
.hint{font-size:13px;color:#555;margin:0}
.err{color:#b3261e;font-size:14px;margin:0;min-height:1em}
.row{display:flex;gap:10px;margin-top:6px}
#log{background:#f1f5f9;border-radius:8px;padding:10px;max-height:220px;overflow:auto;font-size:12px;white-space:pre-wrap;margin:0}
.frow{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #eee;gap:10px}
.empty{padding:32px;text-align:center;color:#444}
@media (max-width:860px){.wrap{padding:16px}.top{margin:-16px -16px 16px;padding:12px 16px}.cols{flex-direction:column;gap:16px}#side{width:auto;position:static;max-height:none;box-sizing:border-box}#main{width:100%}}
</style>
<div class="wrap">
<header class="top"><div id="topic"></div></header><div class="bar"><button id="update" type="button" class="primary">↻ Update blocks</button><button id="export-all" type="button">Export all for Google Docs</button><button id="files-open" type="button">My files</button><button id="settings-open" type="button">⚙ Settings</button></div>
<dialog id="update-box"><form method="dialog" class="dlg">
<h3>Update blocks</h3>
<p class="hint">Downloads every new file from OpenCaselist since your last update, then rebuilds your blocks.</p>
<div id="signin"><label>OpenCaselist email<input id="oc-email" type="email" autocomplete="username"></label>
<label>OpenCaselist password<input id="oc-password" type="password" autocomplete="current-password"></label>
<p class="hint">Your password is kept only while the app is running. It is never saved to a file.</p></div>
<p id="no-caselist" class="hint" hidden>No caselist set, so this will only re-read the files you already have. Add one in ⚙ Settings to download new files.</p>
<div class="row"><button id="update-go" type="button" class="primary">Update now</button><button value="close">Close</button></div>
<hr><p class="hint">Have Word files of your own? <button id="pick" type="button">Add Word files…</button><input id="upload" type="file" accept=".docx" multiple hidden></p>
<pre id="log" hidden></pre></form></dialog>
<dialog id="settings-box"><form method="dialog" class="dlg">
<h3>Settings</h3>
<label>Topic name<input id="s-topic" placeholder="e.g. Resolved: European NATO members should ratify the TPNW"></label>
<label>OpenCaselist caselist<input id="s-caselist" placeholder="e.g. hspf26, or paste the caselist link"></label>
<p class="hint">The part after opencaselist.com/ in your caselist's web address.</p>
<label>Download files posted since<input id="s-since" type="date"></label>
<p class="hint" id="s-since-hint"></p>
<label>Only keep files that mention (optional)<input id="s-keywords" placeholder="e.g. NATO, TPNW, disarmament"></label>
<p class="hint">Separate words with commas. Files that mention them fewer than 3 times are skipped. If you leave this empty, old-topic files are skipped automatically using the words in your topic name.</p>
<label class="check"><input id="s-constructive" type="checkbox"> Include case (1AC/1NC) cards, not only responses (turn off for a smaller, responses-only library)</label>
<p id="s-error" class="err"></p>
<div class="row"><button id="settings-save" type="button" class="primary">Save</button><button value="close">Cancel</button></div>
</form></dialog>
<dialog id="files-box"><form method="dialog" class="dlg">
<h3>My files</h3>
<p class="hint">Files are grouped by month. Deleting a month removes those Word files from this computer and from your blocks. Deleted OpenCaselist months are never downloaded again.</p>
<div id="file-list"></div>
<div class="row"><button id="folder" type="button">Open docs folder</button><button value="close">Close</button></div>
</form></dialog>
<p id="export-help" style="font-size:14px;margin:0 0 20px;color:#555">Downloads the complete organized library as a .docx with original card formatting. In Google Docs, use File → Open → Upload. Export again anytime to include new cards.</p>
<div class="cols">
<div class="box" id="side"></div>
<div id="main"><input id="source" aria-label="School or team" placeholder="Filter by school or team"><input id="search" placeholder="Search argument names and card titles (not citations)"><div id="notice" role="status" aria-live="polite"></div><div id="out"></div></div>
</div></div>
<script>
document.getElementById("topic").textContent=__TOPIC__?"Topic: "+__TOPIC__:"Set your topic in ⚙ Settings";
const $=s=>document.querySelector(s), esc=t=>String(t??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let sel=null, requestId=0, sidebarId=0, formatObserver=null;
const sourceQuery=()=>"source="+encodeURIComponent($("#source").value.trim());
async function j(u){const r=await fetch(u);const data=await r.json();if(!r.ok)throw new Error(data.error||"Request failed");return data}
const richCache=new Map();
async function rich(i){const c=window.cur[i],key=c.id+":"+c.format_version;if(!richCache.has(key))richCache.set(key,j("/api/formatted/"+c.id+"?v="+encodeURIComponent(c.format_version)).catch(e=>{richCache.delete(key);throw e}));return richCache.get(key)}
async function toggleCard(i){
  const el=$("#c"+i),button=el.querySelector(".show");
  if(el.classList.contains("open")){el.classList.remove("open");button.textContent="▸ Show full card";return}
  button.textContent="Loading original formatting…";
  try{const r=await rich(i);el.querySelector(".body").innerHTML=`<div class="source-tag">${r.tag_html}</div><div class="full-cite">${r.cite_html}</div>${r.body_html}`;el.classList.add("open");button.textContent="▾ Hide full card"}
  catch(e){$("#notice").textContent=e.message;button.textContent="▸ Show full card"}
}
// Case cards stay under the side they answer: a Pro case card is a ready answer to Neg arguments.
const CASE=/^\s*(?:case\b\W*)?(?:1ac|1nc|3ac|3nc|constructive|case)\b/i;
const caseLabel=c=>!CASE.test(c.speech||"")?"":c.side==="AT NEG"?"Pro case card":c.side==="AT AFF"?"Con case card":"Case card";
async function side(){
  const generation=++sidebarId, o=await j("/api/outline?"+sourceQuery());if(generation!==sidebarId)return;
  const tot=o.reduce((s,a)=>s+a.n,0);
  if(!tot&&!$("#source").value.trim()){$("#side").innerHTML="<h4>0 CARDS</h4>";$("#out").innerHTML='<div class="box empty"><h3>No blocks yet</h3><p>Click <b>↻ Update blocks</b> to download files from OpenCaselist or add your own Word files.</p></div>';return}
  $("#side").innerHTML=`<h4>${tot} CARDS</h4>`+o.map((s,i)=>
    `<div class="arg" data-side-i="${i}"><span>${esc(s.side)}</span><span class="n">${s.n}</span></div><div id="s${i}" hidden>`+
    s.topics.map((a,j)=>`<div class="topic arg" data-topic-i="${i}-${j}"><span>${esc(a.arg)}</span><span class="n">${a.n}</span></div><div id="t${i}-${j}" hidden>`+
    a.groups.map(g=>`<div class="grp" data-side="${esc(s.side)}" data-a="${esc(a.arg)}" data-g="${esc(g.group)}"><span>${esc(g.group)}</span><span class="n">${g.n}</span></div>`).join("")+`</div>`).join("")+`</div>`).join("");
  document.querySelectorAll("[data-side-i]").forEach(e=>e.onclick=()=>{const g=$("#s"+e.dataset.sideI);g.hidden=!g.hidden});
  document.querySelectorAll("[data-topic-i]").forEach(e=>e.onclick=()=>{const g=$("#t"+e.dataset.topicI);g.hidden=!g.hidden});
  document.querySelectorAll(".grp").forEach(e=>e.onclick=()=>{
    document.querySelectorAll(".grp").forEach(x=>x.classList.remove("on"));e.classList.add("on");
    show(e.dataset.g,e.dataset.a+" · "+e.dataset.side,"/api/cards?side="+encodeURIComponent(e.dataset.side)+"&arg="+encodeURIComponent(e.dataset.a)+"&group="+encodeURIComponent(e.dataset.g)+"&"+sourceQuery())});
  const firstSide=document.querySelector("[data-side-i]");if(firstSide)firstSide.click();
  const firstTopic=document.querySelector("[data-topic-i]");if(firstTopic)firstTopic.click();
  const g=document.querySelector(".grp");if(g)g.click();
}
async function show(title,sub,url){
  const current=++requestId;
  richCache.clear();
  $("#notice").textContent="Loading cards…";
  let cards;
  try{cards=await j(url)}catch(e){if(current===requestId)$("#notice").textContent=e.message;return}
  if(current!==requestId)return;
  $("#notice").textContent=cards.length?"":"No cards found.";
  $("#out").innerHTML=`<h1>${esc(title)}</h1><div class="sub">${esc(sub)} · ${cards.length} cards</div><div class="grid">`+cards.map((c,i)=>{
    const body="";
    const meta=[c.speech,c.source,`${c.teams} team${c.teams==1?"":"s"}`].filter(Boolean).join(" · ");
    return `<div>${meta?`<div class="read">${esc(meta)}</div>`:""}<div class="card" id="c${i}">
      ${caseLabel(c)?`<div class="case">${caseLabel(c)}</div>`:''}<div class="tag">${esc(c.display_tag||c.tag)}</div><div class="cite"><b>Cite:</b> ${esc(c.cite)}</div>
      <div class="preview">${esc(c.preview)}</div>
      <div class="body">${body}</div>
      <span class="show" onclick="toggleCard(${i})">▸ Show full card</span>
      <div class="acts"><a href="/card/${c.id}.docx">Download .docx</a><span onclick="cp(${i})">Copy formatted</span></div></div></div>`}).join("")+"</div>";
  window.cur=cards;
  // Load original formatting only for cards on or near the screen, 4 at a time.
  if(formatObserver)formatObserver.disconnect();
  const queue=[];let active=0;
  const pump=()=>{while(active<4&&queue.length&&current===requestId){active++;loadFormatted(queue.shift()).finally(()=>{active--;pump()})}};
  formatObserver=new IntersectionObserver(entries=>{for(const entry of entries){if(!entry.isIntersecting)continue;
    formatObserver.unobserve(entry.target);queue.push(+entry.target.id.slice(1))}pump()},{rootMargin:"800px 0px"});
  cards.forEach((_,i)=>formatObserver.observe($("#c"+i)));
  async function loadFormatted(i){
    {
      try{
        const r=await rich(i);
        if(current!==requestId)return;
        const el=$("#c"+i);
        el.querySelector(".body").innerHTML=`<div class="source-tag">${r.tag_html}</div><div class="full-cite">${r.cite_html}</div>${r.body_html}`;
        el.querySelector(".cite").innerHTML=r.cite_html;
        el.classList.add("formatted");
      }catch(e){if(current===requestId)$("#notice").textContent="Some original formatting could not be loaded."}
    }
  }
}
async function cp(i){
  let data;
  try{data=await rich(i)}catch(e){$("#notice").textContent=e.message;return}
  const html=data.clipboard_html, plain=data.plain_text;
  if(!navigator.clipboard?.write||typeof ClipboardItem==="undefined"){
    $("#notice").textContent="Formatted clipboard copy is not supported in this browser.";
    return;
  }
  try{
    await navigator.clipboard.write([new ClipboardItem({
      "text/html":new Blob([html],{type:"text/html"}),
      "text/plain":new Blob([plain],{type:"text/plain"})
    })]);
    $("#notice").textContent="Formatted card copied.";
  }catch(error){
    console.error("Could not copy formatted card",error);
    $("#notice").textContent="Couldn't copy the formatted card. Check clipboard permissions and try again.";
  }
}
let t;$("#search").oninput=e=>{clearTimeout(t);t=setTimeout(()=>{
  const v=e.target.value.trim();v?show("Search results",'"'+v+'"',"/api/search?q="+encodeURIComponent(v)+"&"+sourceQuery()):side()},250)};
let sourceTimer;$("#source").oninput=()=>{++requestId;clearTimeout(sourceTimer);sourceTimer=setTimeout(()=>side().catch(e=>$("#notice").textContent=e.message),300)};
async function post(u,data,headers){const r=await fetch(u,{method:"POST",headers:headers||{"Content-Type":"application/json"},body:headers?data:JSON.stringify(data||{})});const out=await r.json();if(!r.ok)throw new Error(out.error||"Request failed");return out}
let config={};
async function loadSettings(){config=await j("/api/settings");return config}
$("#update").onclick=async()=>{try{await loadSettings()}catch(e){$("#notice").textContent="The app isn't responding. Double-click Start, then reload this page.";return}$("#no-caselist").hidden=!!config.caselist;$("#signin").hidden=!config.caselist||config.signed_in;$("#update-box").showModal()};
function showLog(){$("#log").hidden=false}
async function watch(){
  const go=$("#update-go");go.disabled=true;go.textContent="Updating…";$("#update").disabled=true;showLog();
  try{for(;;){const job=await j("/api/job");$("#log").textContent=job.lines.join("\n")+(job.error?"\n⚠ "+job.error:"");$("#log").scrollTop=1e9;if(!job.running)break;await new Promise(r=>setTimeout(r,1000))}}
  finally{go.disabled=false;go.textContent="Update now";$("#update").disabled=false;await loadSettings();$("#signin").hidden=!config.caselist||config.signed_in;side().catch(e=>$("#notice").textContent=e.message)}
}
$("#update-go").onclick=async()=>{
  const download=!!config.caselist;
  try{await post("/api/update",{download,email:$("#oc-email").value.trim(),password:$("#oc-password").value});$("#oc-password").value="";await watch()}
  catch(e){showLog();$("#log").textContent="⚠ "+e.message}
};
$("#pick").onclick=()=>$("#upload").click();
$("#upload").onchange=async e=>{
  const files=[...e.target.files];if(!files.length)return;showLog();$("#log").textContent="";
  try{for(const f of files){await post("/api/upload",f,{"Content-Type":"application/octet-stream","X-Filename":encodeURIComponent(f.name)});$("#log").textContent+="Added "+f.name+"\n"}
    await post("/api/update",{download:false});await watch()}
  catch(err){$("#log").textContent+="⚠ "+err.message}
  e.target.value=""
};
$("#settings-open").onclick=async()=>{try{await loadSettings()}catch(e){$("#notice").textContent="The app isn't responding. Double-click Start, then reload this page.";return}$("#s-topic").value=config.topic;$("#s-caselist").value=config.caselist;$("#s-since").value=config.effective_since;sinceHint();$("#s-keywords").value=config.keywords;$("#s-constructive").checked=config.include_constructive;$("#s-error").textContent="";$("#settings-box").showModal()};
const longDate=d=>new Date(d+"T12:00").toLocaleDateString(undefined,{month:"long",day:"numeric",year:"numeric"});
async function sinceHint(){
  const c=await j("/api/settings?caselist="+encodeURIComponent($("#s-caselist").value.trim()));
  const auto=$("#s-since").value===config.effective_since&&!config.since;if(auto)$("#s-since").value=c.release;
  $("#s-since-hint").innerHTML=`Current topic: <b>${esc(c.current_topic)}</b>, usually released ${esc(longDate(c.release))}. `+
    ($("#s-since").value===c.release?"This date moves forward on its own when the next topic comes out.":`<a href="#" id="s-auto">Use ${esc(longDate(c.release))}</a>`);
  const link=$("#s-auto");if(link)link.onclick=e=>{e.preventDefault();$("#s-since").value=c.release;sinceHint()};
}
$("#s-caselist").onchange=sinceHint;$("#s-since").onchange=sinceHint;
$("#settings-save").onclick=async()=>{
  try{const v=await post("/api/settings",{topic:$("#s-topic").value,caselist:$("#s-caselist").value,since:$("#s-since").value,keywords:$("#s-keywords").value,include_constructive:$("#s-constructive").checked});
    document.getElementById("topic").textContent=v.topic?"Topic: "+v.topic:"Set your topic in ⚙ Settings";$("#settings-box").close();side().catch(()=>{})}
  catch(e){$("#s-error").textContent=e.message}
};
const monthName=m=>m?new Date(m+"-15").toLocaleDateString(undefined,{month:"long",year:"numeric"}):"";
async function fileList(){
  const rows=await j("/api/files");
  $("#file-list").innerHTML=rows.length?rows.map((r,i)=>`<div class="frow"><span><b>${esc(r.origin)}</b> ${esc(monthName(r.month))} · ${r.files} file${r.files==1?"":"s"}</span>${r.month?`<button type="button" data-del="${i}">Delete</button>`:""}</div>`).join(""):'<p class="hint">No files yet.</p>';
  document.querySelectorAll("[data-del]").forEach(b=>b.onclick=async()=>{const r=rows[b.dataset.del];
    if(!confirm(`Permanently delete ${r.files} ${r.origin} file(s) from ${monthName(r.month)}? This can't be undone.`))return;
    try{b.disabled=true;await post("/api/files/delete",{origin:r.origin,month:r.month});$("#files-box").close();$("#update-box").showModal();$("#signin").hidden=true;$("#no-caselist").hidden=true;await watch()}catch(e){alert(e.message);b.disabled=false}})
}
$("#files-open").onclick=async()=>{$("#file-list").innerHTML='<p class="hint">Loading…</p>';$("#files-box").showModal();
  try{await fileList()}catch(e){$("#file-list").innerHTML='<p class="err">The app isn\'t responding. Double-click Start, then reload this page.</p>'}};
$("#folder").onclick=()=>post("/api/open-folder").catch(e=>alert(e.message));
$("#export-all").onclick=async()=>{
  const button=$("#export-all");button.disabled=true;button.textContent="Preparing all cards…";
  $("#notice").textContent="Building the complete library with original source formatting…";
  try{
    const response=await fetch('/export/library.docx');
    if(!response.ok){const error=await response.json();throw new Error(error.error||'Export failed')}
    const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
    link.href=url;link.download='Rebuttal-Blocks.docx';document.body.appendChild(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),60000);
    $("#notice").textContent="Library downloaded. In Google Docs: File → Open → Upload, then select Rebuttal-Blocks.docx.";
  }catch(error){$("#notice").textContent=error.message}
  finally{button.disabled=false;button.textContent="Export all for Google Docs"}
};
side().catch(e=>$("#notice").textContent=e.message);
</script>"""

if __name__ == "__main__":
    TOPIC_OVERRIDE = sys.argv[1] if len(sys.argv) > 1 else ""
    print("Running on http://localhost:8420")
    ThreadingHTTPServer(("127.0.0.1", 8420), H).serve_forever()
