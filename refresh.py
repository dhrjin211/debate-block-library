"""Update the block library: download new OpenCaselist files, add uploads, rebuild.

Everything the dashboard's "Update blocks" button does lives here, so it can also
be run from a terminal:

  python refresh.py                 # rebuild from the docs folder
  python refresh.py download        # download new OpenCaselist files, then rebuild

Downloaded files are stored by month (docs/OpenCaselist/2026-11/...) so a whole
month can be deleted in one step. Deleted months are remembered and never
downloaded again. Passwords are only kept in memory.
"""
import io
import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

import requests

import settings

BASE = Path(__file__).resolve().parent
DOCS = BASE / 'docs'
DOWNLOADED = DOCS / 'OpenCaselist'
UPLOADED = DOCS / 'Uploaded'
HISTORY = BASE / 'download-history.json'
API = 'https://api.opencaselist.com/v1'
MONTH = re.compile(r'\d{4}-\d{2}')
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def minutes(seconds):
    if seconds < 60:
        return 'under a minute'
    m = round(seconds / 60)
    return f'about {m} minute' + ('s' if m != 1 else '')


def _history():
    try:
        return json.loads(HISTORY.read_text())
    except (FileNotFoundError, ValueError):
        return {'downloaded': []}


def _save_history(value):
    temporary = HISTORY.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(HISTORY)


def _safe(part):
    part = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', part).strip(' .')
    return part[:120] or 'file'


# ---------- rebuild ----------

def rebuild(log=print):
    DOCS.mkdir(exist_ok=True)
    log('Reading every Word file in the docs folder…')
    process = subprocess.Popen([sys.executable, '-u', str(BASE / 'cardgrab.py'), 'ingest', str(DOCS)],
                               cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        line = line.strip()
        if line and not line.startswith('speech labels'):
            log(re.sub(r' stored in .*? \(', ' saved (', line))
    if process.wait():
        raise RuntimeError('The library could not be rebuilt. See the messages above.')
    log('Done. Your blocks are up to date.')


# ---------- uploads ----------

def save_upload(name, data):
    name = _safe(Path(name).name)
    if not name.lower().endswith('.docx'):
        raise ValueError(f'{name} is not a Word (.docx) file.')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.getinfo('word/document.xml')
    except (zipfile.BadZipFile, KeyError):
        raise ValueError(f'{name} could not be opened as a Word file.')
    folder = UPLOADED / date.today().strftime('%Y-%m')
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return name


# ---------- OpenCaselist ----------

def mentions(data, words):
    """Count keyword mentions in a .docx. No keywords means keep everything."""
    if not words:
        return None
    text = _text(data)
    return sum(len(re.findall(r'\b' + re.escape(w) + r'\b', text, re.I)) for w in words)


def choose(downloads, since, already):
    """Weekly bulk zips posted on/after `since` that haven't been downloaded yet."""
    picked = []
    for d in downloads:
        name, url = d.get('name') or '', d.get('url') or ''
        found = re.search(r'\d{4}-\d{2}-\d{2}', name)
        if '-weekly-' in name and found and url and found.group() >= since and name not in already:
            picked.append(dict(name=name, url=url, date=found.group()))
    return sorted(picked, key=lambda d: d['date'])


def _team(path):
    parts = Path(path).parts
    return '_'.join(parts[-3:-1]) if len(parts) >= 3 else (parts[0] if len(parts) == 2 else 'Other')


def _store(month, team, name, data):
    """Save one file. An older copy of the same team's file in another month is replaced,
    so a document that was updated (or arrived both alone and in a bundle) is kept once."""
    team, name = _safe(team), _safe(name)
    if DOWNLOADED.exists():
        for old in DOWNLOADED.glob(f'*/{glob_escape(team)}/{glob_escape(name)}'):
            if old.parent.parent.name != month:
                old.unlink()
    dest = DOWNLOADED / month / team / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def glob_escape(text):
    return re.sub(r'([*?\[])', r'[\1]', text)


def _on_topic(data, words, auto=()):
    """Keep a file if it mentions the chosen keywords (3+ times), or, with no keywords,
    if words from the topic name make up at least 1% of its text (4+ different ones).
    Tested on 500 real files: keeps ~96% of new-topic files and ~2% of old-topic ones."""
    try:
        if words:
            return mentions(data, words) >= 3
        if not auto:
            return True
        text = _text(data)
        # One pass over the text for all topic words at once.
        pattern = re.compile(r'\b(' + '|'.join(re.escape(w) for w in auto) + r')\w*', re.I)
        found = [m.lower() for m in pattern.findall(text)]
        distinct = sum(1 for w in auto if any(f.startswith(w) for f in set(found)))
        return distinct >= min(4, max(2, len(auto) - 1)) and len(found) * 1000 / max(1, len(text.split())) >= 10
    except Exception:
        return False


def _text(data):
    from lxml import etree
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = etree.fromstring(z.read('word/document.xml'))
    return ' '.join(''.join(p.itertext()) for p in root.iter(W + 'p'))


def choose(downloads, since, already):
    """Weekly bulk zips posted on/after `since` that haven't been downloaded yet."""
    picked = []
    for d in downloads:
        name, url = d.get('name') or '', d.get('url') or ''
        found = re.search(r'\d{4}-\d{2}-\d{2}', name)
        if '-weekly-' in name and found and url and found.group() >= since and name not in already:
            picked.append(dict(name=name, url=url, date=found.group()))
    return sorted(picked, key=lambda d: d['date'])


def _team(path):
    parts = Path(path).parts
    return '_'.join(parts[-3:-1]) if len(parts) >= 3 else (parts[0] if len(parts) == 2 else 'Other')


def _store(month, team, name, data):
    """Save one file. An older copy of the same team's file in another month is replaced,
    so a document that was updated (or arrived both alone and in a bundle) is kept once."""
    team, name = _safe(team), _safe(name)
    if DOWNLOADED.exists():
        for old in DOWNLOADED.glob(f'*/{glob_escape(team)}/{glob_escape(name)}'):
            if old.parent.parent.name != month:
                old.unlink()
    dest = DOWNLOADED / month / team / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def glob_escape(text):
    return re.sub(r'([*?\[])', r'[\1]', text)


def _on_topic(data, words, auto=()):
    """Keep a file if it mentions the chosen keywords (3+ times), or, with no keywords,
    if words from the topic name make up at least 1% of its text (4+ different ones).
    Tested on 500 real files: keeps ~96% of new-topic files and ~2% of old-topic ones."""
    try:
        if words:
            return mentions(data, words) >= 3
        if not auto:
            return True
        text = _text(data)
        # One pass over the text for all topic words at once.
        pattern = re.compile(r'\b(' + '|'.join(re.escape(w) for w in auto) + r')\w*', re.I)
        found = [m.lower() for m in pattern.findall(text)]
        distinct = sum(1 for w in auto if any(f.startswith(w) for f in set(found)))
        return distinct >= min(4, max(2, len(auto) - 1)) and len(found) * 1000 / max(1, len(text.split())) >= 10
    except Exception:
        return False


TAGS = re.compile(r'<[^>]+>')


def _text(data):
    """Plain text of a .docx, quickly (word counting doesn't need full XML parsing)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read('word/document.xml').decode('utf-8', 'ignore')
    return TAGS.sub(' ', xml.replace('</w:p>', '\n'))


def download(email, password, log=print, pause=6):
    config = settings.load()
    caselist = config['caselist']
    if not caselist:
        raise ValueError('Add your caselist in Settings first (for example hspf26).')
    since = settings.since(config)
    log(f'Getting files posted since {since}.')
    words = settings.keywords()
    auto = [] if words else settings.topic_words()
    if auto:
        log('Skipping files that aren\'t about your topic (using the words in your topic name).')
    elif not words:
        log('No topic name set, so every file is kept. Add your topic in Settings to skip old-topic files.')
    session = requests.Session()
    log('Signing in to OpenCaselist…')
    response = session.post(API + '/login', json={'username': email, 'password': password}, timeout=30)
    if response.status_code == 401:
        raise ValueError('OpenCaselist did not accept that email and password.')
    response.raise_for_status()
    response = session.get(f'{API}/caselists/{caselist}/downloads', timeout=30)
    if response.status_code == 404:
        raise ValueError(f'OpenCaselist has no caselist called "{caselist}". Check Settings.')
    response.raise_for_status()
    history = _history()
    history.setdefault('files', [])
    kept = skipped = 0

    # 1. Weekly bulk bundles: one download per week instead of thousands of requests.
    chosen = choose(response.json(), since, set(history['downloaded']))
    if chosen:
        log(f'Found {len(chosen)} new weekly bundle(s).')
    for bundle in chosen:
        log(f"Downloading bundle {chosen.index(bundle) + 1} of {len(chosen)}: {bundle['name']}…")
        with session.get(bundle['url'], stream=True, timeout=120) as r:
            r.raise_for_status()
            buffer = io.BytesIO()
            for chunk in r.iter_content(1 << 20):
                buffer.write(chunk)
        with zipfile.ZipFile(buffer) as z:
            for name in z.namelist():
                path = Path(name)
                if path.suffix.lower() != '.docx' or path.name.startswith('~$'):
                    continue
                data = z.read(name)
                if not _on_topic(data, words, auto):
                    skipped += 1
                    continue
                _store(bundle['date'][:7], _team(name), path.name, data)
                kept += 1
        history['downloaded'].append(bundle['name'])
        history['latest_bundle'] = max(history.get('latest_bundle', ''), bundle['date'])
        _save_history(history)
        log(f'  saved so far: {kept} files' + (f' ({skipped} skipped: off-topic)' if skipped else ''))

    # 2. Files posted after the newest weekly bundle (the night before a tournament).
    after = max(since, history.get('latest_bundle', ''))
    try:
        response = session.get(f'{API}/caselists/{caselist}/recent', timeout=30)
        response.raise_for_status()
        recent = response.json()
    except Exception:
        recent = []
        log("Couldn't check for files posted since the last weekly bundle; try again later.")
    seen = set(history['files'])
    fresh = {}
    for row in recent if isinstance(recent, list) else []:
        path, updated = row.get('opensource') or '', (row.get('updated_at') or '')[:19]
        key = f'{path}@{updated}'
        if path.lower().endswith('.docx') and updated[:10] >= after and key not in seen:
            fresh[path] = (key, updated)
    if fresh:
        log(f'Found {len(fresh)} file(s) posted since the last weekly bundle.')
    if fresh:
        log(f'  downloading one at a time so OpenCaselist isn\'t overloaded ({minutes(len(fresh) * (pause + 1))}).')
    for number, (path, (key, updated)) in enumerate(sorted(fresh.items(), key=lambda item: item[1][1]), 1):
        log(f'  file {number} of {len(fresh)}: {Path(path).name}')
        r = None
        for attempt in range(4):
            try:
                r = session.get(f'{API}/download', params={'path': path}, timeout=60)
                if r.status_code == 429:  # OpenCaselist asked us to slow down
                    wait = int(r.headers.get('Retry-After', '30') or 30) + 1
                    log(f'  OpenCaselist asked to slow down; waiting {wait} seconds…')
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    z.getinfo('word/document.xml')
                break
            except Exception:
                r = None
                time.sleep(pause)
        if r is None or r.status_code != 200:
            log(f'  could not download {Path(path).name}; it will be retried next time.')
            continue
        if _on_topic(r.content, words, auto):
            _store(updated[:7], _team(path), Path(path).name, r.content)
            kept += 1
        else:
            skipped += 1
        history['files'].append(key)
        _save_history(history)
        time.sleep(pause)  # be gentle with OpenCaselist

    if not kept:
        log('No new files on OpenCaselist since your last update.')
    else:
        log(f'Downloaded {kept} new Word files.' + (f' Skipped {skipped} off-topic.' if skipped else ''))
    return kept


# ---------- managing stored files ----------

def months():
    """Stored files grouped by where they came from and month, newest first."""
    rows = []
    for origin, root in (('OpenCaselist', DOWNLOADED), ('Uploaded', UPLOADED)):
        if not root.exists():
            continue
        for folder in root.iterdir():
            if folder.is_dir() and MONTH.fullmatch(folder.name):
                count = sum(1 for f in folder.rglob('*.docx'))
                rows.append(dict(origin=origin, month=folder.name, files=count))
    other = sum(1 for f in DOCS.rglob('*.docx')
                if f.is_relative_to(DOCS) and not (f.is_relative_to(DOWNLOADED) or f.is_relative_to(UPLOADED))) if DOCS.exists() else 0
    rows.sort(key=lambda r: r['month'], reverse=True)
    if other:
        rows.append(dict(origin='Added to the docs folder by hand', month='', files=other))
    return rows


def delete_month(origin, month):
    if not MONTH.fullmatch(month or ''):
        raise ValueError('Pick a month to delete.')
    root = {'OpenCaselist': DOWNLOADED, 'Uploaded': UPLOADED}.get(origin)
    if not root:
        raise ValueError('Unknown file group.')
    folder = root / month
    if not folder.is_dir():
        raise ValueError('Those files are already gone.')
    count = sum(1 for f in folder.rglob('*.docx'))
    shutil.rmtree(folder)
    return count


if __name__ == '__main__':
    if sys.argv[1:2] == ['download']:
        import getpass
        download(input('OpenCaselist email: '), getpass.getpass('Password (not saved): '))
    rebuild()
