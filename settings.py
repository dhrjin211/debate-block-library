"""User settings, edited from the Settings panel in the dashboard and stored in settings.json."""
import json
import re
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
PATH = BASE / 'settings.json'
DEFAULTS = {
    'topic': '',                  # shown at the top of the dashboard and in exports
    'caselist': '',               # OpenCaselist caselist name, e.g. hspf26
    'since': '',                  # custom start date (YYYY-MM-DD); empty = current topic's release date
    'keywords': '',               # optional, comma-separated: keep only files that mention these
    'include_constructive': True,   # also keep 1AC/1NC case cards, not only responses
    'show_theory': None,          # theory, framework (phil) and K cards; None = by event (off for PF)
}


def load():
    values = dict(DEFAULTS)
    try:
        values.update({k: v for k, v in json.loads(PATH.read_text()).items() if k in DEFAULTS})
    except (FileNotFoundError, ValueError):
        pass
    return values


def save(changes):
    values = load()
    for key, value in (changes or {}).items():
        if key not in DEFAULTS:
            continue
        if key in ('include_constructive', 'show_theory'):
            values[key] = None if value is None else bool(value)
            continue
        value = str(value or '').strip()
        if key == 'caselist':
            # Accept a pasted link such as https://opencaselist.com/hspf26/School
            match = re.search(r'opencaselist\.com/([A-Za-z0-9_-]+)', value)
            value = (match.group(1) if match else value).strip('/').lower()
            if value and not re.fullmatch(r'[a-z0-9_-]{2,40}', value):
                raise ValueError('The caselist name should look like hspf26 (letters and numbers only).')
        if key == 'since' and value and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('Use a date like 2026-10-01.')
        values[key] = value[:200]
    if values['since'] == current_topic(values['caselist'])[1].isoformat():
        values['since'] = ''  # stay automatic so the next topic is picked up on its own
    temporary = PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(values, indent=2))
    temporary.replace(PATH)
    return values


# Words that appear in most resolutions and say nothing about the topic.
COMMON = set("""resolved should ought the a an of on in to for and or its their it be by with from that this
which not no more than less substantially increase decrease reduce significantly federal government united states
us usa adopt implement policy policies ban prohibit require requires ought justified unjust morally moral
permissible impermissible generally all any some other between among against into over under
through via using use within must has have been were would will can could than also""".split())


def topic_words():
    """Distinctive words from the topic name, used to skip old-topic files automatically."""
    words = []
    for word in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", load()['topic']):
        word = word.lower().strip("'-")
        if word not in COMMON and word not in words:
            words.append(word)
    return words


def keywords():
    return [k.strip() for k in load()['keywords'].split(',') if k.strip()]


# Usual NSDA release dates: (month, day, topic it starts).
# The active topic is the latest release on or before today. Users can override
# the date in Settings when a release moves.
SCHEDULES = {
    'pf': ('Public Forum', [(8, 1, 'September–October'), (10, 1, 'November–December'), (12, 1, 'January'),
                           (1, 1, 'February'), (2, 1, 'March'), (3, 1, 'April'), (5, 1, 'Nationals')]),
    'ld': ('Lincoln-Douglas', [(8, 1, 'September–October'), (10, 1, 'November–December'),
                              (12, 1, 'January–February'), (2, 1, 'March–April'), (5, 1, 'Nationals')]),
    'policy': ('Policy', [(6, 1, 'season')]),
}


def event(caselist):
    name = (caselist or '').lower()
    if 'pf' in name:
        return 'pf'
    if 'ld' in name:
        return 'ld'
    if 'policy' in name or 'cx' in name or name.startswith('ndtceda'):
        return 'policy'
    return 'pf'


def current_topic(caselist='', today=None):
    """(description, release date) of the topic being debated today."""
    today = today or date.today()
    event_name, releases = SCHEDULES[event(caselist)]
    found = None
    for year in (today.year - 1, today.year):
        for month, day, label in releases:
            released = date(year, month, day)
            if released <= today and (found is None or released > found[1]):
                topic_year = year + 1 if month == 12 and label.startswith('Jan') else year
                if label == 'season':
                    label = f'{year}–{str(year + 1)[2:]}'
                elif label == 'Nationals':
                    label = f'Nationals {year}'
                else:
                    label = f'{label} {topic_year}'
                found = (f'{label} {event_name}', released)
    return found


def show_theory(values=None):
    """Theory, framework (phil) and K cards are shown in LD and Policy, hidden in PF,
    unless the user chose otherwise in Settings."""
    values = values or load()
    if values.get('show_theory') is not None:
        return bool(values['show_theory'])
    return event(values['caselist']) != 'pf'


def since(values=None):
    """The date downloads start from: the custom date, or the current topic's release."""
    values = values or load()
    return values['since'] or current_topic(values['caselist'])[1].isoformat()
