"""
cardgrab.py: parse Verbatim .docx debate files into cards and search them.

  python cardgrab.py ingest ./docs        # parse every .docx under ./docs into cards.db
  python cardgrab.py reorganize           # reorganize an existing cards.db without rereading .docx files
  python cardgrab.py search "nuclear deterrence" [limit]

Supports Verbatim and citation-backed tags in ordinary/title/table paragraphs.
Indexing and formatted exports share the original source paragraph boundaries.
"""
import json
import re
import sqlite3
import sys
from os import replace
from collections import Counter
from pathlib import Path

from docx import Document
from organize import classify, veto, looks_like_citation

BASE_DIR = Path(__file__).resolve().parent
DB = str(BASE_DIR / "cards.db")


def include_constructive_evidence():
    import settings
    return bool(settings.load()['include_constructive'])


HEADINGS = {"Heading 1", "Heading 2", "Heading 3", "Heading 4"}
# speech headings (H1 or H2) split a doc into 1AC, 1NC, 2AC, 2NC, Rebuttal and so on
SPEECH = re.compile(r"^\s*(1ac|1nc|2ac|2nc|1ar|1nr|2nr|2ar|3ac|3nc|constructive|case|reb(?:uttal)?s?|summary"
                    r"|final\s?focus|front\s?lines?|blocks?)\b", re.I)
RESPONSE = re.compile(r"^\s*(2ac|2nc|1ar|1nr|2nr|2ar|reb(?:uttal)?s?|front\s?lines?)\b", re.I)
CONSTRUCTIVE = re.compile(r"^\s*(1ac|1nc|3ac|3nc|constructive|case)\b", re.I)
ANSWER = re.compile(r"^\s*(?:a\s*/\s*2|a2|a\s*/\s*t|at)\b", re.I)
RESPONSE_CLAIM = re.compile(r"^\s*(?:\d+[\].):\-]\s*)?(?:\[(?:NL|NU|NQ|T|DL|LT)\]|(?:no|non[- ]?unique|not unique)\b)|\b(?:their|aff|neg)\s+(?:evidence|card|case|argument|warrant)|\b(?:no link|does not|doesn't|cannot|can't|won't|fails? to|rife with ambiguity|no enforcement)\b", re.I)
PREFIX = re.compile(r"^\s*(?:a\s*/\s*2|a2|a\s*/\s*t|at)\b[\s:\u2013\u2014-]*", re.I)
NOISE = re.compile(r"\b(1ac|1nc|2ac|2nc|1nr|2nr|2ar|3ac|3nc|cards?)\b", re.I)
GENERIC_TOPICS = {
    "", "case", "their case", "our case", "other", "responses", "response",
    "their", "our", "god", "overview", "off", "offcase", "off case",
    "top shelf", "topshelf", "general responses", "da", "adv", "aff",
    "neg", "k", "t", "fw", "party", "frontline", "frontlines",
}
TOPIC_ALIASES = {
    "econ": "Economy", "economics": "Economy", "economy": "Economy",
    "grid": "Grid", "grids": "Grid", "electricity": "Grid",
    "warming": "Climate", "global warming": "Climate",
    "climate": "Climate", "water": "Water",
    "environment": "Environment", "environmental": "Environment",
    "clean energy": "Clean Energy", "renewables": "Clean Energy",
    "renewable energy": "Clean Energy",
    "artificial intelligence": "AI", "agi": "AI",
    "reg": "Regulations", "regs": "Regulations",
    "regulation": "Regulations", "regulations": "Regulations",
}
def parse(path):
    from cardparse import extract
    return extract(path)[1]


def _clean_topic(value):
    value = PREFIX.sub("", value.strip())
    value = re.split(r"\s*---\s*", value, maxsplit=1)[0]
    value = NOISE.sub("", value)
    value = re.sub(r"^\s*aff(?:irmative)?\s*[:\-]\s*|^\s*neg(?:ative)?\s*[:\-]\s*", "", value, flags=re.I)
    value = re.sub(r"^\s*\d{1,3}\s*[\]\).:-]\s*", "", value)
    value = re.sub(r"^\s*(?:NL|NUQ|NQ|OV)\b[\s:.-]*", "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" .:-–—")
    value = re.sub(r"\b(?:their|our)\s+case\b", "", value, flags=re.I).strip(" .:-–—")
    value = re.sub(r"\b(?:case|responses?)\b", "", value, flags=re.I).strip(" .:-–—")
    if value.casefold() in GENERIC_TOPICS or not re.search(r"[a-z0-9]", value, re.I):
        return ""
    # Some source headings repeat their own title (including the AT prefix).
    words = value.split()
    for split in range(1, len(words)):
        left, right = " ".join(words[:split]), " ".join(words[split:])
        if left.casefold() == PREFIX.sub("", right).casefold():
            value = left
            break
    return TOPIC_ALIASES.get(value.casefold(), value)


def _is_response_heading(value):
    value = _clean_topic(value).casefold()
    words = re.findall(r"[a-z]+", value)
    response_words = {
        "no", "and", "or", "link", "links", "internal", "causal", "takeout",
        "turn", "turns", "solvency", "implementation", "impact", "defense",
        "mitigation", "unique", "uniqueness", "non", "other",
    }
    strategy_words = response_words - {"no", "and", "or"}
    return bool(words and set(words) <= response_words and set(words) & strategy_words)


def _topic(c):
    topic, reason, _ = classify(c)
    c["_reason"] = reason  # saved with the card so the dashboard doesn't re-sort every card
    return topic


def _side(speech):
    match = re.match(r"\s*(2ac|1ar|2ar|3ac|2nc|1nr|2nr|3nc)\b", speech, re.I)
    if not match:
        return "Other / unclear"
    return "AT NEG" if match.group(1).lower() in {"2ac", "1ar", "2ar", "3ac"} else "AT AFF"


def _caselist_side(source_file):
    role = re.search(r"(?:^|[-_])(pro|aff|affirmative|con|neg|negative)(?:[-_]|$)",
                     Path(source_file).stem, re.I)
    if not role:
        return "Other / unclear"
    return "AT NEG" if role.group(1).lower() in {"pro", "aff", "affirmative"} else "AT AFF"


def _response_type(c):
    # A local response label is stronger evidence than prose or a parent heading.
    shorthand = re.match(r"^\s*(?:\d+[\].):\-]\s*)?(NU/T|NL/T|DL/T|LT|T|TURN|NU|NQ|NUQ|N/U|DL|NL|NIL|ID|UQ)\s*[:.\-–—]", c["tag"], re.I)
    if shorthand:
        return _response_type_text("[" + shorthand.group(1) + "]")
    for value in (c["tag"], c.get("block_heading", c["block"]),
                  c.get("arg_heading", c["arg"])):
        kind = _response_type_text(value)
        if kind != "Defense / other":
            return kind
    return "Defense / other"


def _response_type_text(label_text):
    opening = ""
    text = label_text
    markers = {marker.upper().replace(" ", "") for marker in re.findall(r"\[([^\]]{1,16})\]", label_text)}
    explicit_types = (
        ({"T", "TURN", "LT", "L/T", "NU/T", "NL/T", "DL/T"}, "Turn"),
        ({"NQ", "NUQ", "NU", "N/U"}, "Non-uniqueness"),
        ({"DL"}, "Disadvantage Link (DL)"),
        ({"UQ", "UNIQ", "UNIQUENESS"}, "Uniqueness"),
        ({"NL", "NIL", "!D"}, "Link defense"),
        ({"ID", "IMPACTDEFENSE"}, "Impact mitigation"),
    )
    for labels, kind in explicit_types:
        if markers & labels:
            return kind
    if markers & {"NQ", "NUQ", "NU", "N/U"} or re.search(
        r"\b(?:NQ|NUQ|NU|N/U)\b|\b(?:non[- ]?unique|not unique|no uniqueness)\b", text, re.I
    ):
        return "Non-uniqueness"
    if bool(markers & {"DL"}) or re.search(
        r"\b(?:DL|disadvantage link)\b", label_text, re.I
    ):
        return "Disadvantage Link (DL)"
    if markers & {"T", "TURN", "LT", "L/T", "NU/T", "NL/T", "DL/T"} or re.search(
        r"(?:^\s*(?:\d+[\].):\-]\s*)?(?:link )?turn\b|\bturns? the (?:case|argument|impact)\b)", label_text, re.I
    ) or re.search(
        r"\b(?:turn(?:s|ing|ed)?|turn case|turns? the case)\b", opening, re.I
    ):
        return "Turn"
    if markers & {"UQ", "UNIQ", "UNIQUENESS"} or re.search(r"^\s*(?:uniqueness|unique)\s*(?:$|[:—–-])", text, re.I):
        return "Uniqueness"
    if markers & {"NL", "NIL", "!D"} or re.search(
        r"\b(?:no link|no internal link|no causal link|link defense|link takeout|"
        r"alt(?:ernative)? causes?|(?:doesn.t|does not|will not|won.t) cause|"
        r"not caused by|fails? to cause)\b", text, re.I
    ):
        return "Link defense"
    if markers & {"ID", "IMPACTDEFENSE"} or re.search(
        r"\b(?:mitigat(?:e|es|ed|ing|ion)|impact defense|no impact|impact takeout|survivable|"
        r"not existential|no extinction|overstat(?:ed|e)|overhyped|overblown|exaggerat(?:ed|es)|"
        r"highly unlikely|minor impact)\b",
        text, re.I
    ) or re.search(
        r"\beven if\b.{0,80}\b(?:cut|eliminat|reduc|stop|ban|limit)\w*\b", text, re.I
    ):
        return "Impact mitigation"
    if re.search(r"\b(?:solv(?:e|es|ed|ency|ing))\b", text, re.I):
        return "Solvency"
    if re.search(r"^\s*impact(?:\s+|\s*[:\-])", label_text, re.I) or re.search(
        r"\b(?:impact defense|no impact|impact takeout)\b", text, re.I
    ):
        return "Impact"
    return "Defense / other"


def label(c, names, keep_all, source_file=""):
    """Return (side, topic, response type), or None for non-responses/invalid cards."""
    if veto(c):
        return None
    constructive = CONSTRUCTIVE.match(c["speech"])
    response_speech = RESPONSE.match(c["speech"])
    speech_name = re.match(r"\s*(1ac|1nc|3ac|3nc)\b", c["speech"], re.I)
    answers = any(ANSWER.match(c.get(key, "")) for key in ("arg_heading", "block_heading", "arg", "block"))
    if not keep_all and constructive and (
        not speech_name or speech_name.group(1).lower() != "3ac" or not answers
    ):
        return None
    direct_answer = bool(source_file) and not constructive and bool(RESPONSE_CLAIM.search(c['tag']))
    if not (response_speech or answers or direct_answer):
        if not keep_all:
            return None
        side = _side(c["speech"])
        return (_caselist_side(source_file) if side == "Other / unclear" else side,
                _topic(c), _response_type(c))
    side = _side(c["speech"]) if response_speech or constructive else "Other / unclear"
    if side == "Other / unclear":
        side = _caselist_side(source_file)
    topic = _topic(c)
    topic = names.setdefault(topic.casefold(), topic)
    return side, topic, _response_type(c)


CARD_FIELDS = ("tag", "cite", "body", "read", "marked", "speech", "arg", "block")
CACHE = BASE_DIR / "parse-cache.db"


def _parse_file(path):
    """Worker: parse one .docx into plain card fields (no Word objects)."""
    try:
        return path, [{k: c[k] for k in CARD_FIELDS} for c in parse(path)], None
    except Exception as error:
        return path, None, str(error) or type(error).__name__


def _parser_version():
    import hashlib
    code = b"".join((BASE_DIR / name).read_bytes() for name in ("cardparse.py",))
    return hashlib.sha256(code).hexdigest()[:16]


def _open_cache():
    con = sqlite3.connect(CACHE)
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INT, mtime INT, cards TEXT)")
    # Cards from one file after sorting into side / argument / response type.
    con.execute("CREATE TABLE IF NOT EXISTS sorted (path TEXT PRIMARY KEY, stamp TEXT, rows TEXT, seen INT, speeches TEXT)")
    version = _parser_version()
    row = con.execute("SELECT value FROM meta WHERE key='version'").fetchone()
    if not row or row[0] != version:
        # The reading rules changed: every file is read again.
        con.execute("DELETE FROM files")
        con.execute("DELETE FROM sorted")
        con.execute("INSERT OR REPLACE INTO meta VALUES ('version', ?)", (version,))
    return con


def _minutes(seconds):
    if seconds < 60:
        return "under a minute"
    minutes = round(seconds / 60)
    return f"about {minutes} minute{'s' if minutes != 1 else ''}"


def read_files(files, workers=None, unchanged=0):
    """Parse files in parallel, reusing earlier results for files that haven't changed.
    Prints progress with an estimate of the time left."""
    import os
    import time
    from concurrent.futures import ProcessPoolExecutor, as_completed
    con = _open_cache()
    known = {path: (size, mtime, cards) for path, size, mtime, cards in con.execute("SELECT * FROM files")}
    results, todo = {}, []
    for f in files:
        stat = f.stat()
        hit = known.get(str(f))
        if hit and hit[0] == stat.st_size and hit[1] == stat.st_mtime_ns:
            results[f] = json.loads(hit[2])  # None = a broken file we already reported
        else:
            todo.append(f)
    unchanged += len(results)
    if unchanged:
        print(f"{unchanged} files unchanged since last time; reading {len(todo)} new or changed "
              f"file{'s' if len(todo) != 1 else ''}.", flush=True)
    else:
        print(f"Reading {len(todo)} file{'s' if len(todo) != 1 else ''}.", flush=True)
    started = last = time.monotonic()
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(_parse_file, f) for f in todo]):
            path, cards, error = future.result()
            done += 1
            if error:
                print(f"skipped {path.name}: {error}", flush=True)
            results[path] = cards  # None for a broken file, remembered so it isn't retried
            stat = path.stat()
            con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)",
                        (str(path), stat.st_size, stat.st_mtime_ns, json.dumps(cards)))
            now = time.monotonic()
            if now - last >= 3 or done == len(todo):
                last = now
                left = (now - started) / done * (len(todo) - done)
                print(f"Reading files: {done} of {len(todo)}" +
                      (f" · {_minutes(left)} left" if done < len(todo) else " · done"), flush=True)
                con.commit()
    con.commit()
    con.close()
    return results


def _forget_missing(files):
    """Drop saved results for files that were deleted (for example a removed month)."""
    con = _open_cache()
    present = {str(f) for f in files}
    for table in ("files", "sorted"):
        stale = [(p,) for (p,) in con.execute(f"SELECT path FROM {table}") if p not in present]
        con.executemany(f"DELETE FROM {table} WHERE path=?", stale)
    con.commit()
    con.close()


def _library_signature(files, keep_all):
    """Fingerprint of everything a rebuild depends on: the files, the case-card
    setting, and the code that reads and sorts cards."""
    import hashlib
    digest = hashlib.sha256(str(keep_all).encode())
    for name in ("cardparse.py", "cardgrab.py", "organize.py"):
        digest.update((BASE_DIR / name).read_bytes())
    for f in files:
        stat = f.stat()
        digest.update(f"{f}|{stat.st_size}|{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def ingest(folder, keep_all=False):
    import os
    keep_all = keep_all or include_constructive_evidence()
    database = Path(DB)
    files = [f for f in sorted(Path(folder).rglob("*.docx")) if not f.name.startswith("~$")]
    signature = _library_signature(files, keep_all)
    signature_file = database.with_suffix(database.suffix + ".signature")
    if database.exists() and signature_file.exists() and signature_file.read_text() == signature:
        print("Nothing changed since the last update, so there was nothing to read.", flush=True)
        return
    rebuilding = database.with_suffix(f"{database.suffix}.rebuilding-{os.getpid()}")
    rebuilding.unlink(missing_ok=True)
    con = sqlite3.connect(rebuilding)
    con.execute("CREATE VIRTUAL TABLE cards USING fts5"
                "(tag, cite, body, read, side UNINDEXED, arg, block, speech UNINDEXED, "
                "source_file UNINDEXED, arg_heading UNINDEXED, block_heading UNINDEXED, "
                "marked UNINDEXED, source UNINDEXED, reason UNINDEXED)")
    n = seen = files_seen = 0
    names, speeches = {}, Counter()
    try:
        import hashlib
        sort_version = hashlib.sha256(str(keep_all).encode() + b"".join(
            (BASE_DIR / name).read_bytes() for name in ("cardgrab.py", "organize.py"))).hexdigest()[:16]
        _forget_missing(files)
        cache = _open_cache()
        stamps = {}
        for f in files:
            stat = f.stat()
            stamps[f] = f"{stat.st_size}|{stat.st_mtime_ns}|{sort_version}"
        saved = {path: (stamp, rows, count, said) for path, stamp, rows, count, said in cache.execute("SELECT * FROM sorted")}
        todo = [f for f in files if saved.get(str(f), ("",))[0] != stamps[f]]
        parsed = read_files(todo, unchanged=len(files) - len(todo)) if todo else {}
        if len(todo) > 50:
            print(f"Sorting cards from {len(todo)} files…", flush=True)
        for f in files:
            if f in parsed or str(f) not in saved:
                found = parsed.get(f)
                rows, count, said = [], 0, Counter()
                if found is not None:
                    count = len(found)
                    said.update((c["speech"].split("-")[0].strip().lower()[:12] or "(none)") for c in found)
                    source_file = f.relative_to(Path(folder)).as_posix()
                    # Team folder name; uploads sit in month folders, so use the file name there.
                    source = (f.parent.name if f.parent != Path(folder) and not re.fullmatch(r"\d{4}-\d{2}", f.parent.name)
                              else f.stem)
                    for c in found:
                        placement = label(c, {}, keep_all, source_file)
                        if not placement:
                            continue
                        side, topic, response_type = placement
                        speech = c["speech"] or (
                            "A/2 / AT heading" if ANSWER.match(c["arg"]) or ANSWER.match(c["block"])
                            else "Unlabeled section"
                        )
                        rows.append((c["tag"], c["cite"], " ".join(c["body"]), " ".join(c["read"]), side, topic,
                                     response_type, speech, source_file,
                                     c["arg"], c["block"], "\n".join(c["marked"]), source, c.get("_reason", "")))
                cache.execute("INSERT OR REPLACE INTO sorted VALUES (?,?,?,?,?)",
                              (str(f), stamps[f], json.dumps(rows), count, json.dumps(said)))
            else:
                _, rows, count, said = saved[str(f)]
                rows, said = json.loads(rows), Counter(json.loads(said))
            if count:
                files_seen += 1
            seen += count
            speeches.update(said)
            for row in rows:
                row = list(row)
                row[5] = names.setdefault(row[5].casefold(), row[5])  # same spelling for one argument
                con.execute("INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            n += len(rows)
        cache.commit()
        cache.close()
        con.commit()
    except BaseException:
        con.close()
        rebuilding.unlink(missing_ok=True)
        raise
    con.close()
    replace(rebuilding, database)
    signature_file.write_text(signature)
    if keep_all:
        print(f"{n} cards stored in {DB} (responses and case cards; {seen} found in the files)")
    else:
        print(f"{n} response cards stored in {DB} (case cards left out; {seen} found in the files)")
    print("speech labels seen:", ", ".join(f"{k} {v}" for k, v in speeches.most_common(12)))


def reorganize():
    """Reclassify a database when it still contains its original speech and headings."""
    con = sqlite3.connect(DB)
    columns = {row[1] for row in con.execute("PRAGMA table_info(cards)")}
    required = {"tag", "cite", "body", "read", "block", "arg", "marked", "source"}
    if not required <= columns:
        raise RuntimeError("cards.db does not have the expected legacy cards table")

    organized = "side" in columns
    if organized:
        if "speech" not in columns:
            raise RuntimeError("cards.db lacks original speech labels; rebuild it with `cardgrab.py ingest docs`")
        has_headings = {"arg_heading", "block_heading"} <= columns
        extras = ", arg_heading, block_heading" if has_headings else ""
        rows = con.execute(
            f"SELECT tag, cite, body, read, side, arg, block, speech, source_file, marked, source{extras} "
            "FROM cards"
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT tag, cite, body, read, block, arg, marked, source FROM cards"
        ).fetchall()
    con.execute("BEGIN")
    con.execute("DROP TABLE IF EXISTS cards_organized")
    con.execute("CREATE VIRTUAL TABLE cards_organized USING fts5"
                "(tag, cite, body, read, side UNINDEXED, arg, block, speech UNINDEXED, "
                "source_file UNINDEXED, arg_heading UNINDEXED, block_heading UNINDEXED, "
                "marked UNINDEXED, source UNINDEXED, reason UNINDEXED)")
    n = 0
    for row in rows:
        card = {}
        if organized:
            tag, cite, body, read, old_side, old_topic, old_type, speech, source_file, marked, source, *headings = row
            arg_heading, block_heading = headings if headings else ("", "")
            if headings:
                card = {"tag": tag, "cite": cite, "read": read, "body": [body], "arg": arg_heading, "block": block_heading,
                        "speech": speech}
                label_value = label(card, {}, include_constructive_evidence(), source_file)
                if not label_value:
                    continue
                side, topic, response_type = label_value
            else:
                side, topic, response_type = old_side, old_topic, old_type
        else:
            tag, cite, body, read, speech, old_topic, marked, source = row
            card = {"tag": tag, "cite": cite, "read": read, "body": [body], "arg": old_topic, "block": "", "speech": speech}
            label_value = label(card, {}, include_constructive_evidence())
            if not label_value:
                continue
            side, topic, response_type = label_value
            source_file = arg_heading = block_heading = ""
        reason = card.get("_reason", "")
        con.execute("INSERT INTO cards_organized VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (tag, cite, body, read, side, topic, response_type, speech, source_file,
                     arg_heading, block_heading, marked, source, reason))
        n += 1
    con.execute("DROP TABLE cards")
    con.execute("ALTER TABLE cards_organized RENAME TO cards")
    con.commit()
    print(f"Reorganized {n} response cards in {DB}")


def search(query, limit=5):
    con = sqlite3.connect(DB)
    q = " ".join(f'"{w}"' for w in query.split())
    rows = con.execute("SELECT tag, cite, read, source FROM cards WHERE cards MATCH ? "
                       "ORDER BY rank LIMIT ?", (q, limit)).fetchall()
    for tag, cite, read, source in rows:
        print(f"\n{tag}\n{cite}\n{read[:400]}\n[{source}]")
    if not rows:
        print("no matches")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ingest" and len(sys.argv) > 2:
        ingest(sys.argv[2], "--all" in sys.argv)
    elif cmd == "reorganize":
        reorganize()
    elif cmd == "search" and len(sys.argv) > 2:
        search(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 5)
    else:
        print(__doc__)
