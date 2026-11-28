"""Controlled debate topics and an auditable card-level placement pass.

Never promote source prose, response labels, tournament names or placeholders to topics.
A known answer heading wins; otherwise use the claim and selected evidence together.
Ambiguous cards stay in a dedicated review bucket instead of inventing a category.
"""
import re
import unicodedata
from collections import Counter
from functools import lru_cache

REVIEW = 'Unsorted'
PLACEHOLDER = re.compile(r'\b(?:placeholder[\s_\-]*\d*|insert\s+(?:card|tag|text)|todo|tbd|lorem ipsum|text condensed)\b|^\s*<<\s*(?:inserted|.*for reference)\s*>>\s*$', re.I)
# Built-in categories are general debate impacts and argument types that come up on
# every topic. Nothing here is tied to one resolution: topic-specific arguments get
# their categories from the files' own headings (see heading_label), so a new topic
# sorts itself without code changes. Each expression is an independent signal; generic
# words such as AI do not outweigh a specific energy, military or economic mechanism.
RULES = {
 # Security and conflict
 'Nuclear War': (r'\b(?:nuclear (?:war|warfare|exchange|escalation|conflict|use|strikes?|winter|terrorism)|accidental launch|miscalculation|entanglement|first.strike|near.miss\w*|nukes)\b',),
 'Deterrence': (r'\b(?:deterren\w*|deter|nuclear umbrella|extended deterrence|second.strikes?|mutual(?:ly)? assured destruction|security guarantees?)\b',),
 'Proliferation': (r'\b(?:proliferat\w*|non.?proliferation|npt|nuclear acquisition|denucleari[sz]\w*|nuclear breakout|enrichment)\b',),
 'Arms Control': (r'\b(?:arms control|disarmament|arms race|treaty compliance|ratif\w*|verification|safeguards|arms treaty|new start|test ban)\b',),
 'Bioweapons': (r'\b(?:bioweapons?|biological weapons?|biowarfare|bioterror\w*)\b',),
 'Terrorism': (r'\b(?:terroris\w*|terror attacks?|extremis\w*|insurgen\w*|isis|al.qaeda|radicali[sz]\w*)\b',),
 'Military and Security': (r'\b(?:military|warfare|missiles?|great power (?:war|conflict)|defen[cs]e industrial|readiness|hypersonic\w*|conventional (?:war|weapons?|forces)|troops|arms sales)\b',),
 'Military Tech': (r'\b(?:autonomous weapons?|killer robots?|drones?|military ai|nc3|command and control|hyperwar)\b',),
 'Cybersecurity': (r'\b(?:cyber\w*|hacking|hackers?|ransomware)\b',),
 'Space': (r'\b(?:outer space|space (?:race|debris|militari[sz]ation|weapons?)|orbital|satellites?|lunar|asat)\b',),
 'NATO and Alliances': (r'\b(?:nato|alliances?|alliance cohesion|article (?:5|five)|burden.sharing|collective defen[cs]e|allies)\b',),
 'Diplomacy': (r'\b(?:diplomac\w*|diplomatic|detente|negotiations?|multilateral\w*|united nations|soft power)\b',),
 # Regions
 'Russia and Ukraine': (r'\b(?:russia|russian|ukraine|ukrainian|putin|kremlin)\b',),
 'China and Taiwan': (r'\b(?:china|chinese|beijing|taiwan|xi jinping|ccp|indo.?pacific|south china sea)\b',),
 'Korea and Japan': (r'\b(?:south korea|north korea|korean|pyongyang|japan|japanese)\b',),
 'Middle East': (r'\b(?:middle east|iran|iranian|israel\w*|gaza|saudi|syria\w*|iraq\w*|yemen|hezbollah|hamas)\b',),
 'India and Pakistan': (r'\b(?:india|indian|pakistan\w*|kashmir|modi)\b',),
 # Economy and society
 'Economy': (r'\b(?:econ(?:omy|omic|omics)?|recession|gdp|inflation|economic growth|financial (?:crisis|markets?)|stock markets?|jobs|employment|unemployment|wages|debt|bubbles?|investment|affordab\w*|prices)\b',),
 'Trade': (r'\b(?:trade (?:war|deficit|deals?|polic\w*)|tariffs?|exports?|imports?|supply chains?|offshor\w*|outsourc\w*|reshoring|protectionis\w*)\b',),
 'Inequality and Poverty': (r'\b(?:inequality|poverty|low.income|wealth gap|working class|the poor|marginali[sz]ed)\b',),
 'Energy': (r'\b(?:energ(?:y|ies)|electric(?:ity|al)? grids?|power grids?|grids?|power plants?|blackouts?|fossil fuels?|oil|natural gas|renewables?|solar|wind power|nuclear power|nuclear energy|fusion|utilities|ratepayers?)\b',),
 'Climate': (r'\b(?:climate|global warming|warming|carbon emissions?|greenhouse|decarboni[sz]\w*|emissions?|net.zero)\b',),
 'Environment': (r'\b(?:environment(?:al)?|pollution|biodiversity|ecosystems?|deforestation|air quality|toxic)\b',),
 'Water': (r'\b(?:water (?:scarcity|shortages?|supply|use|wars?)|drought|aquifers?|freshwater)\b',),
 'Agriculture': (r'\b(?:agricultur\w*|farmers?|farming|crops?|fertili[sz]er|food security|food prices|famine)\b',),
 'Healthcare': (r'\b(?:healthcare|health care|medical|medicine|hospitals?|public health|pandemics?|disease|vaccines?|mental health)\b',),
 'Education': (r'\b(?:education\w*|schools?|students?|teachers?|curriculum|k.12)\b',),
 'Immigration': (r'\b(?:immigra\w*|migrants?|refugees?|asylum|border security|deportation\w*)\b',),
 'Criminal Justice': (r'\b(?:criminal justice|police|policing|prisons?|incarcerat\w*|crime|sentencing)\b',),
 'Human Rights': (r'\b(?:human rights|humanitarian|civil liberties|civil rights|genocide|war crimes|reparations|freedom of speech|free speech)\b',),
 'Privacy': (r'\b(?:privacy|surveillance|data collection|personal data|biometric\w*|facial recognition)\b',),
 'Democracy': (r'\b(?:democrac(?:y|ies)|democratic (?:institutions|backsliding|norms)|authoritarian\w*|autocra\w*|rule of law|misinformation|disinformation)\b',),
 'Elections and Politics': (r'\b(?:midterms?|elections?|electoral|democrats?|republicans?|gop|voters?|polari[sz]ation|partisan\w*|congress|political capital)\b',),
 'Communities': (r'\b(?:communit(?:y|ies)|indigenous|tribal|native nations?|housing|land use|local residents|rural)\b',),
 'Regulations': (r'\b(?:regulat\w*|enforcement|compliance|loopholes?|federalism|preemption|state governments?|patchwork|zoning|red tape|10th amendment)\b',),
 # Technology
 'Innovation': (r'\b(?:innovation|competition|monopol\w*|antitrust|breakthroughs?|r\s?&\s?d|startups?)\b',),
 'Tech Race': (r'\b(?:tech(?:nology)? race|ai race|tech(?:nological)? leadership|ai leadership|ai dominance|race with china)\b',),
 'AI': (r'\b(?:ai|artificial intelligence|machine learning|compute|data centers?|algorithms?)\b',),
 'AI Safety': (r'\b(?:alignment|misalign\w*|rogue ai|ai safety|ai risk|existential risk from ai|superintelligen\w*|asi|agi)\b',),
 'Semiconductors': (r'\b(?:semiconductors?|microchips?|chips?|export controls|chipmak\w*)\b',),
 'Minerals and Materials': (r'\b(?:critical minerals?|minerals?|mining|rare earths?|lithium|cobalt|copper|steel|aluminu?m)\b',),
 'Quantum': (r'\bquantum\b',),
 # Debate theory
 'Framework': (r'\b(?:framework|framing|utilitarian\w*|extinction first|weighing|ethics|morality|presumption|role of the ballot|rotb|virtue|deontolog\w*|skepticism|epistem\w*|structural violence)\b',),
 'Theory and Topicality': (r'\b(?:topicality|theory|conditionality|counterplans?|permutations?|perm do|we meet|fiat|jurisdiction|normal means|plan focus|tricks|trix|spreading|disclosure)\b',),
 'Critiques': (r'\b(?:capitalism|psychoanalysis|settler colonial\w*|setcol|racism|antiblackness|afropessimism|ableism|feminis\w*|necropolitic\w*|security k|deleuze|baudrillard|hyperreality|marx\w*|biopolitic\w*|militarism|neoliberal\w*)\b',),
 'Speculative Impacts': (r'\b(?:aliens?|extraterrestrial|fermi paradox|multiverse|vacuum decay|time travel|simulation hypothesis|we live in a simulation)\b',),
}
COMPILED = {k: tuple(re.compile(p, re.I) for p in v) for k, v in RULES.items()}
# Exact heading aliases: short headings debaters use on every topic.
ALIASES = {
 # security
 'nuclear war':'Nuclear War','nuke war':'Nuclear War','nukes':'Nuclear War','nuclear winter':'Nuclear War','miscalc':'Nuclear War',
 'miscalculation':'Nuclear War','escalation':'Nuclear War','nuclear escalation':'Nuclear War','accidents':'Nuclear War','entanglement':'Nuclear War',
 'deterrence':'Deterrence','nuclear deterrence':'Deterrence','conventional deterrence':'Deterrence','extended deterrence':'Deterrence',
 'umbrella':'Deterrence','nuclear umbrella':'Deterrence','coercion':'Deterrence','credibility':'Deterrence',
 'proliferation':'Proliferation','prolif':'Proliferation','nonproliferation':'Proliferation','nonprolif':'Proliferation','npt':'Proliferation',
 'arms control':'Arms Control','disarmament':'Arms Control','arms race':'Arms Control','verification':'Arms Control','ratification':'Arms Control',
 'bioweapons':'Bioweapons','biological weapons':'Bioweapons','bioterror':'Bioweapons','bioterrorism':'Bioweapons',
 'terrorism':'Terrorism','terror':'Terrorism','terrorists':'Terrorism','nuclear terrorism':'Terrorism',
 'military':'Military and Security','heg':'Military and Security','hegemony':'Military and Security','readiness':'Military and Security',
 'conventional':'Military and Security','hypersonics':'Military and Security','great power war':'Military and Security',
 'military ai':'Military Tech','laws':'Military Tech','drones':'Military Tech','autonomous weapons':'Military Tech',
 'cyber':'Cybersecurity','cybersecurity':'Cybersecurity','cyberattacks':'Cybersecurity','space':'Space','space war':'Space',
 'nato':'NATO and Alliances','alliances':'NATO and Alliances','alliance cohesion':'NATO and Alliances','allies':'NATO and Alliances',
 'diplomacy':'Diplomacy','diplo':'Diplomacy','dipplomacy':'Diplomacy','detente':'Diplomacy','soft power':'Diplomacy',
 # regions
 'russia':'Russia and Ukraine','ukraine':'Russia and Ukraine','china':'China and Taiwan','taiwan':'China and Taiwan',
 'south korea':'Korea and Japan','north korea':'Korea and Japan','korea':'Korea and Japan','japan':'Korea and Japan',
 'iran':'Middle East','israel':'Middle East','middle east':'Middle East','india':'India and Pakistan','pakistan':'India and Pakistan',
 # economy and society
 'econ':'Economy','economics':'Economy','economy':'Economy','econony':'Economy','recession':'Economy','jobs':'Economy',
 'inflation':'Economy','investment':'Economy','bubble':'Economy','bubbles':'Economy','austerity':'Economy',
 'trade':'Trade','tariffs':'Trade','supply chains':'Trade','offshoring':'Trade','outsourcing':'Trade',
 'inequality':'Inequality and Poverty','poverty':'Inequality and Poverty',
 'energy':'Energy','grid':'Energy','grids':'Energy','grid collapse':'Energy','electricity':'Energy','electricity prices':'Energy',
 'energy prices':'Energy','clean energy':'Energy','renewables':'Energy','nuclear power':'Energy','nuclear energy':'Energy','fusion':'Energy','oil':'Energy',
 'climate':'Climate','warming':'Climate','climate change':'Climate','emissions':'Climate',
 'environment':'Environment','environmental':'Environment','enviorment':'Environment','pollution':'Environment','biodiversity':'Environment',
 'water':'Water','agriculture':'Agriculture','food':'Agriculture','food security':'Agriculture',
 'healthcare':'Healthcare','health':'Healthcare','medical':'Healthcare','medicine':'Healthcare','pandemics':'Healthcare','disease':'Healthcare',
 'education':'Education','schools':'Education','immigration':'Immigration','refugees':'Immigration',
 'crime':'Criminal Justice','policing':'Criminal Justice','prisons':'Criminal Justice',
 'human rights':'Human Rights','humanitarian':'Human Rights','privacy':'Privacy','surveillance':'Privacy',
 'democracy':'Democracy','authoritarianism':'Democracy','elections':'Elections and Politics','midterms':'Elections and Politics',
 'politics':'Elections and Politics','polarization':'Elections and Politics','political capital':'Elections and Politics',
 'regulations':'Regulations','regs':'Regulations','federalism':'Regulations','enforcement':'Regulations',
 # technology
 'innovation':'Innovation','competition':'Innovation','tech race':'Tech Race','ai race':'Tech Race','ai leadership':'Tech Race',
 'ai':'AI','cloud':'AI','agi':'AI Safety','misalignment':'AI Safety','alignment':'AI Safety','ai safety':'AI Safety',
 'semis':'Semiconductors','semiconductors':'Semiconductors','chips':'Semiconductors','minerals':'Minerals and Materials','quantum':'Quantum',
 # theory
 'framework':'Framework','fw':'Framework','framing':'Framework','extinction first':'Framework','extinction comes first':'Framework',
 'sv':'Framework','structural violence':'Framework','util':'Framework','weighing':'Framework',
 't':'Theory and Topicality','topicality':'Theory and Topicality','theory':'Theory and Topicality','disclosure':'Theory and Topicality',
 'condo':'Theory and Topicality','perm':'Theory and Topicality','k':'Critiques','kritik':'Critiques','cap':'Critiques','capitalism':'Critiques',
 'aliens':'Speculative Impacts','spark':'Speculative Impacts',
}
# Theory, philosophy and kritik headings (hidden in Public Forum unless turned on).
ALIASES.update({w: 'Theory and Topicality' for w in (
    'spec','aspec','ospec','spikes','tricks','trix','rvi','rvis','pics','paragraph theory','nibs',
    'condo bad','condo good','disclosure theory','theory shells','t shells','fairness','framework theory')})
ALIASES.update({w: 'Framework' for w in (
    'kant','kantianism','utilitarianism','deont','deontology','contractualism','rawls','levinas',
    'pragmatism','skep','skepticism','determinism','consequentialism','phil','virtue ethics',
    'moral uncertainty','ethics','util good','util bad','value','value criterion','criterion')})
ALIASES.update({w: 'Critiques' for w in (
    'cap k','set col','setcol','afropessimism','afropess','psychoanalysis','psycho','baudrillard','fem',
    'fem k','security k','biopower','queer','queerness','ableism','orientalism','imperialism','deleuze',
    'nietzsche','k aff','kritik','kritiks','ks','the k','alt','alternative','root cause','ontology')})
GENERIC = {'','aff','neg','pro','con','cards','card','link','links','off','offs','on','analytics','analytic','extensions','extension','extention','ext','misc','new','other stuff','args','arguments','evidence','random','backlines','backline','2ac','2nc','1ar','1nr','a2','at','adv','da','cp','plan','general','responses','response','overview','ov','o v','u','uq','l','lt','nl','nq','nu','turn','turns','solvency','link defense','impact defense','impact mitigation','defense','impact','case','other unclear','other','top level','core','c1','c2','c3','offense','blocks','rebuttal','frontline',
 'carded evidence','carded','answers','answer to neg','answer to aff','answers to neg','answers to aff','aff constructive','neg constructive',
 'constructive','pro constructive','con constructive','contention','1ac','1nc','speech doc','vs case','on case','off case','case args','case turns'}


def plain(text):
    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def clean_heading(value):
    value = (value or '').strip()
    if len(value) > 140 or len(value.split()) > 18 or PLACEHOLDER.search(value):
        return ''
    value = re.sub(r'^\s*(?:[123](?:ac|nc|ar|nr)|rebuttal)\b[\s:—–-]*', '', value, flags=re.I)
    value = re.sub(r'^\s*\d+[\].):\-]\s*', '', value)
    value = re.sub(r'^\s*(?:a\s*/\s*[2t]|a2|at)\s*[:\-–—]*\s*', '', value, flags=re.I)
    value = re.sub(r'^\s*(?:c\d|contention\s*\d|da|adv|t|nl|nil|nq|nu|lt|dl)\s*[:.\-–—]\s*', '', value, flags=re.I)
    value = re.sub(r'\s*(?:da|adv|topshelf|ov|overview)\s*$', '', value, flags=re.I)
    value = re.sub(r'\([^)]*\)|\s*(?:->|---).*$', '', value)
    return plain(value)


@lru_cache(maxsize=8192)
def _signals(text):
    return tuple((topic, len({plain(m.group()) for p in patterns for m in p.finditer(text or '')}))
                 for topic, patterns in COMPILED.items())


def signals(text):
    # Cache immutable counts; heading tie-breaking must not mutate shared results.
    return Counter(dict(_signals(text or '')))


THEORY = {'Framework', 'Theory and Topicality', 'Critiques'}
_TOPIC = [None, '']


def current_topic():
    """The topic name from Settings (re-read only when settings.json changes)."""
    try:
        import settings
        stamp = settings.PATH.stat().st_mtime_ns if settings.PATH.exists() else 0
        if _TOPIC[0] != stamp:
            _TOPIC[:] = [stamp, settings.load()['topic']]
    except Exception:
        return ''
    return _TOPIC[1]


@lru_cache(maxsize=32)
def topic_categories(topic):
    """Built-in categories the resolution itself is about.

    On a topic about arms sales, "Military and Security" would swallow almost every
    card, so those cards are sorted by their own headings instead (AT: Saudi, AT:
    Readiness, ...). The broad category is only a last resort for cards with no heading."""
    return frozenset(t for t, patterns in COMPILED.items()
                     if t not in THEORY and any(p.search(topic or '') for p in patterns))


def heading_topic(value, skip=frozenset()):
    cleaned = clean_heading(value)
    if cleaned in GENERIC or not cleaned:
        return None
    if cleaned in ALIASES:
        return ALIASES[cleaned] if ALIASES[cleaned] not in skip else None
    # Near-misspellings of a known heading ("detterence") join that category.
    if len(cleaned) >= 6:
        import difflib
        close = difflib.get_close_matches(cleaned, [k for k in ALIASES if len(k) >= 6 and abs(len(k) - len(cleaned)) <= 1], n=1, cutoff=0.88)
        if close:
            return ALIASES[close[0]] if ALIASES[close[0]] not in skip else None
    scores = signals(cleaned)
    for t in skip:
        scores[t] = 0
    if scores['Energy']:
        return 'Energy'
    # A sentence about AI and energy belongs in Energy; AI alone is too generic.
    if any(scores[t] for t in scores if t != 'AI'):
        scores['AI'] = 0
    ranked = scores.most_common()
    if ranked and ranked[0][1] and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
        return ranked[0][0]
    return None


ANSWER_PREFIX = re.compile(r'^\s*(?:a\s*/\s*[2t]|a2|at)\b', re.I)


def heading_label(value):
    """A short, readable category name taken from a source heading, or ''."""
    if not clean_heading(value) or clean_heading(value) in GENERIC:
        return ''
    text = re.sub(r'\s*(?:->|---|—|–|\|).*$', '', value or '')
    text = re.sub(r'^\s*(?:[123](?:ac|nc|ar|nr)|rebuttal)\b[\s:—–-]*', '', text, flags=re.I)
    text = re.sub(r'^\s*\d+[\].):\-]\s*', '', text)
    text = re.sub(r'^\s*(?:a\s*/\s*[2t]|a2|at)\b\s*[:\-–—]*\s*', '', text, flags=re.I)
    text = re.sub(r'\([^)]*\)', '', text)
    # Drop argument-type labels: "DA -- Big Yahu", "Relations ADV", "K: Cap".
    text = re.sub(r'^\s*(?:da|adv|cp|k|t|off)\b\s*[:\-–—]*\s*', '', text, flags=re.I)
    text = re.sub(r'\s*\b(?:da|adv|advantage|disad|disadvantage)\s*$', '', text, flags=re.I)
    text = re.sub(r'\s+', ' ', text).strip(' .:;,-')
    words = text.split()
    if not 1 <= len(words) <= 5 or len(text) > 40 or not re.search(r'[A-Za-z]{2}', text):
        return ''
    if plain(text) in GENERIC:
        return ''
    if text == text.lower():
        text = ' '.join(w[:1].upper() + w[1:] for w in words)
    return text


def classify(card, topic=None):
    skip = topic_categories(current_topic() if topic is None else topic)
    headings = [card.get('block_heading',card.get('block','')), card.get('arg_heading',card.get('arg',''))]
    for h in headings:
        if len(h.split()) <= 8 and len(h) <= 85:
            topic = heading_topic(h, skip)
            if topic:
                return topic, 'Recognized answer heading', False
    # Any topic: an explicit "AT ..." heading the built-in list doesn't know
    # becomes its own category instead of being guessed from body text.
    for h in headings[::-1]:
        if ANSWER_PREFIX.match(h or ''):
            name = heading_label(h)
            if name:
                return name, 'Answer heading from the source', False
    tag = card.get('tag','')
    tag_scores = signals(tag)
    read = card.get('read','')
    if isinstance(read,list): read=' '.join(read)
    body = card.get('body','')
    if isinstance(body,list): body=' '.join(body)
    selected_scores = signals(read)
    body_scores = signals(body[:4000])
    all_scores = Counter({t: 5*min(tag_scores[t],3) + 2*min(selected_scores[t],3) + min(body_scores[t],3) for t in RULES})
    scores = Counter({t: v for t, v in all_scores.items() if t not in skip})
    if any(scores[t] >= 5 for t in scores if t != 'AI'):
        scores['AI'] = 0
    ranked = scores.most_common(2)
    best,score = ranked[0]
    for h in headings:
        topic = heading_topic(h, skip)
        if topic:
            # Long source claims can be stale/mis-styled headings. Veto a mismatch
            # when this card's own claim and evidence support a different mechanism.
            sentence_heading = len(h.split()) > 8 or len(h) > 85
            if sentence_heading and best != topic and tag_scores[best] and score >= 5 and score-ranked[1][1]>=2:
                return best, 'Card evidence vetoed a conflicting sentence heading', False
            return topic, 'Recognized answer heading', False
    # Require a strong claim signal plus corroboration, or repeated evidence signals.
    if score >= 5 and score - ranked[1][1] >= 2:
        return best, 'Card claim and evidence signals', False
    for h in headings[::-1]:
        name = heading_label(h)
        if name:
            return name, 'Short source heading', False
    # Last resort: the broad category the resolution is about.
    for h in headings:
        topic = heading_topic(h)
        if topic in skip:
            return topic, 'Topic-wide category (no clearer heading)', False
    wide = Counter({t: all_scores[t] for t in skip}).most_common(1)
    if wide and wide[0][1] >= 5:
        return wide[0][0], 'Topic-wide category (no clearer heading)', False
    return REVIEW, 'Ambiguous target; review original claim and evidence', True


def veto(card):
    tag = (card.get('tag') or '').strip()
    body = card.get('body') or ''
    if isinstance(body,list): body=' '.join(body)
    if PLACEHOLDER.search(tag):
        return 'Placeholder tag in source'
    if re.fullmatch(r"[A-Z][A-Za-z’' -]{1,45}\s+['‘’]?(?:19|20)?\d{2}[.,]?",tag):
        return 'Citation-only paragraph lacks a card claim'
    if not tag or not body.strip():
        return 'Missing tag or evidence'
    if not (card.get('cite') or '').strip():
        return 'Missing citation'
    return None


def display_title(tag):
    text = re.sub(r'\s+', ' ', tag or '').strip()
    # Only shorten the browser label. Full original tag remains in the card/export.
    if len(text) <= 150:
        return text
    cut = text.rfind(' ',0,147)
    return text[:cut if cut > 60 else 147].rstrip(' ,;:-') + '…'


def looks_like_citation(text):
    return bool(re.search(r'https?://|www\.',text or '',re.I) and
                re.search(r"(?:\b(?:19|20)\d{2}\b|(?<!\d)['’]?\d{2}(?!\d))",(text or '')[:100]))


# ---------- shared tags ----------
# When several teams read the same evidence, each wrote its own tag. Tags written
# for one round ("Their whole case is wrong", "Extend our Smith card") read oddly
# when shared, so the card shows the clearest general tag among the teams' own.
RELATIVE = re.compile(r"\b(?:their|they|they're|them|theirs|our|ours|we|we're|opponents?|my|"
                      r"extend\w*|cross.?apply|same card|this card|that card|card|the aff|the neg|aff|neg|"
                      r"pro|con|case|contention|1ac|1nc|2ac|2nc|1ar|2ar|1nr|2nr|"
                      r"turns?\s+(?:the|their|it)|answers?\s+(?:the|their)|responds?|round|judge|ballot|voters?|"
                      r"weigh\w*|prefer|framing|also|secondly|thirdly|otherwise|ultimately|else)\b", re.I)
_LEADING = re.compile(r"^\s*(?:\[?\d+\s*[\]\).:\-–—]|[-–—*•]+|[a-z]\)|(?:a2|at)\s*:|and\b|but\b|also\b|first(?:ly)?\b|second(?:ly)?\b|third(?:ly)?\b|finally\b)[\s,;:]*", re.I)


def _tag_words(tag):
    return {w for w in re.findall(r"[a-z]{3,}", (tag or '').casefold())}


def best_tag(tags, current=None, margin=1.5, evidence=''):
    """The clearest, most general tag from several teams' tags for one card.

    With `current` (the tag the card already shows), switch only when another tag
    scores clearly higher, so a fine tag is never swapped for a sideways one."""
    cleaned = []
    for tag in tags:
        tag = _LEADING.sub('', re.sub(r'\s+', ' ', tag or '')).strip(' ,;:-–—')
        if tag and not PLACEHOLDER.search(tag) and not re.fullmatch(r'(?i)tag\s*\d*|card\s*\d*|\d+', tag):
            cleaned.append(tag)
    # Only real sentences can replace another tag: no one-word labels or citations.
    def usable(tag):
        return len(tag.split()) >= 4 and not looks_like_citation(tag) and \
            not re.match(r"^[A-Z][\w'’-]+\s+(?:et al\.?\s+)?['’]?\d{2,4}\b", tag)
    if not cleaned:
        return None
    counts = Counter(t.casefold() for t in cleaned)
    unique = list({t.casefold(): t for t in cleaned}.values())
    if len(unique) == 1:
        return unique[0]
    words = {t: _tag_words(t) for t in unique}
    said = _tag_words(evidence)

    def score(tag):
        mine = words[tag]
        others = [words[o] for o in unique if o is not tag]
        # A tag that shares wording with the other teams' tags says what they agree on.
        overlap = sum(len(mine & o) / max(1, len(mine | o)) for o in others) / max(1, len(others))
        n = len(tag.split())
        length = 0 if 5 <= n <= 25 else -1 if n < 5 else -0.5
        length -= 1 if tag[:1].islower() else 0  # a fragment cut from a longer tag
        relative = len(RELATIVE.findall(tag)) + len(re.findall(r'\bus\b', tag))  # "us", not "US"
        # A good tag says what the evidence says.
        grounded = len(mine & said) / max(1, len(mine)) if said else 0
        return 3 * overlap + 2 * grounded + 0.6 * counts[tag.casefold()] + length - 1.5 * relative

    candidates = [t for t in unique if usable(t)] or unique
    best = max(candidates, key=lambda t: (score(t), -abs(len(t.split()) - 14)))
    if current:
        mine = _LEADING.sub('', re.sub(r'\s+', ' ', current)).strip()
        same = next((t for t in unique if t.casefold() == mine.casefold()), None)
        if same is not None and score(best) - score(same) < margin:
            return same
    return best
