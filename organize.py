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
PLACEHOLDER = re.compile(r'\b(?:placeholder[\s_\-]*\d*|insert\s+(?:card|tag|text)|todo|tbd|lorem ipsum)\b|^\s*<<\s*(?:inserted|.*for reference)\s*>>\s*$', re.I)
# Each expression is an independent signal. Generic words such as AI/data center
# do not outweigh a specific energy, water, military or economic mechanism.
RULES = {
 'Conventional Arms': (r'\b(?:hypersonic\w*|conventional (?:shift|weapons?|arms|forces|war)|precision.strike)\b',),
 'Bioweapons': (r'\b(?:bioweapons?|biological weapons?|biowarfare|biological warfare)\b',),
 'UK Influence': (r'\b(?:sudan|british influence|uk influence|security council|unsc|permanent seat)\b',),
 'Nuclear Deterrence': (r'\b(?:deterren\w*|nuclear umbrella|extended deterrence|second.st[ri]+kes?|nuclear (?:security )?guarantee|mutual assured destruction|mad doctrine)\b',),
 'NATO and Alliances': (r'\b(?:nato|north atlantic treaty organization|alliance cohesion|article (?:5|five)|burden.sharing|collective defen[cs]e)\b',),
 'Disarmament': (r'\b(?:disarmament|abolition|abolish nuclear|nuclear ban|eliminat\w* nuclear weapons|nuclear.zero)\b',),
 'Nonproliferation': (r'\b(?:non.proliferation|proliferat\w*|npt|non.proliferation treaty|horizontal proliferation|vertical proliferation|nuclear acquisition|denucleari[sz]\w*)\b',),
 'Treaty Compliance': (r'\b(?:tpnw|treaty on the prohibition|ratif\w*|verification|safeguards|treaty compliance|treaty enforcement|withdrawal clause|loopholes|clandestine|cheating|no enforcement)\b',),
 'Nuclear Risk': (r'\b(?:nuclear war|nuclear winter|accidental launch|miscalculation|entanglement|first.strike|near.detonations?|nuclear escalation|nuclear terrorism|nukes)\b',),
 'Russia and Ukraine': (r'\b(?:russia|russian|ukraine|ukrainian|putin)\b',),
 'Korea and Japan': (r'\b(?:south korea|north korea|korean|japan|japanese)\b',),
 'Nuclear Justice': (r'\b(?:nuclear colonialism|nuclear test(?:ing|s)?|hibakusha|victim assi[st]+ance|victim remediation|remediation|reparations?|reperations?|compensation|humanitarian consequences|articles? 6\s*(?:&|and)\s*7)\b',),
 'Divestment': (r'\b(?:divest\w*|disinvest\w*|nuclear (?:financ\w*|investment)|nuclear.weapons? (?:financ\w*|producers?|manufacturers?)|don.t bank on the bomb)\b',),
 'Energy': (r'\b(?:energ(?:y|ies)|electric(?:ity|al)|grids?|power plants?|power supply|power demand|power generation|power outage|blackouts?|gas.fired|fossil fuels?|renewables?|solar|wind power|fusion|nuclear energy|smrs?|utility|utilities|ratepayers?)\b',),
 'Water': (r'\b(?:water|drought|aquifers?|cooling water)\b',),
 'Climate': (r'\b(?:climate|warming|carbon emissions?|carbon sinks?|greenhouse|decarboni[sz]|emissions?)\b',),
 'Environment': (r'\b(?:environment(?:al)?|pollution|biodiversity|ecosystems?|air quality|asthma)\b',),
 'AI Investment': (r'\b(?:bubbles?|capex|ipo|stocks?|stock market|ai investment|issuance|debt issuance|bailouts?)\b',),
 'Economy': (r'\b(?:econ(?:omy|omic|omics|ony)?|recession|gdp|inflation|economic growth|financial|markets?|affordability|affordibility|prices?|jobs|employment|inequality|taxes)\b',),
 'Midterms': (r'\b(?:midterms?|elections?|democrats?|republicans?|gop|voters?|credit claiming|golden dome)\b',),
 'Offshoring': (r'\b(?:offshor\w*|outsourc\w*)\b',),
 'Tech Race': (r'\b(?:tech race|ai race|ai leadership|us leadership|ai dominance|technology leadership|race with china|tech dev)\b',),
 'China and Taiwan': (r'\b(?:china|chinese|taiwan|indopac|indo pacific)\b',),
 'Diplomacy': (r'\b(?:diplomac\w*|dipplomacy|diplo|detente|alliances?|cooperation|diplomatic)\b',),
 'Military AI': (r'\b(?:military ai|autonomous weapons?|laws|isr|nc3|command and control|hyperwar|modern warfare|mobilization|military capabilities)\b',),
 'Military and Security': (r'\b(?:military|warfare|missile|golden dome|great power war|defense industrial|aukus)\b',),
 'Cybersecurity': (r'\b(?:cyber\w*|hacking|hackers?)\b',),
 'AI Safety': (r'\b(?:alignment|misalign\w*|rogue ai|ai safety|ai safeguards|ai risk|s.risks?|asi|agi impossible|artificial general intelligence)\b',),
 'Healthcare': (r'\b(?:healthcare|health care|medical|medicine|biotech|phages?|amrs?|public health|pandemics?|bioterror)\b',),
 'Agriculture': (r'\b(?:agricultur\w*|farmers?|farming|fertili[sz]er|bees|honey|food security)\b',),
 'Semiconductors': (r'\b(?:semiconductors?|semi conductors?|chips?|circuit building|export controls)\b',),
 'Minerals and Materials': (r'\b(?:minerals?|mining|copper|aluminu?m|aluminium|metals?|steel|rare earth|commodities|material supply)\b',),
 'Quantum': (r'\bquantum\b',),
 'Space': (r'\b(?:space|orbital|satellites?|moon|lunar|rockets?|stargate)\b',),
 'Democracy': (r'\b(?:democracy|democratic institutions|authoritarian\w*|surveillance|citizen privacy)\b',),
 'Communities': (r'\b(?:communit\w*|indigenous|tribal|native nations?|housing|land use|local residents)\b',),
 'Regulations': (r'\b(?:regulat\w*|enforcement|circumven\w*|exemptions?|grandfather\w*|splitting|downsizing|smaller data centers|small data centers|rollback|zoning|patchwork|implementation|federalism|states solve|state governments?|10th amendment)\b',),
 'Innovation': (r'\b(?:innovation|competition|monopol\w*|antitrust|breakthroughs?|green tech)\b',),
 'AI': (r'\b(?:ai|artificial intelligence|agi|compute|cloud computing|slms?)\b',),
 'Framework': (r'\b(?:framework|framing|pragmatism|utilitarian\w*|extinction first|extinction 1st|ex 1st|weighing|ethics|hedonism|morality|presumption|rotb|virtue|taoism|skepticism|skep|epistem\w*)\b',),
 'Theory and Topicality': (r'\b(?:topicality|theory|conditionality|counterplan|permutation|perm do|we meet|fiat|jurisdiction|normal means|plan focus|trix|spreading|consent)\b',),
 'Critiques': (r'\b(?:capitalism|bataille|baudrill\w*|psychoanalysis|setcol|settler|racism|ableism|fem|necrop\w*|undercommons|security k|cap good|k proper|k style|deleuze|hyperreality|marx|blackness|freire|revolutionary praxis)\b',),
 'Speculative Impacts': (r'\b(?:aliens?|ashtar|fermi|multiverse|multi verse|vacuum decay|time travel|simulation|jupiter|grey draconian|omega points|optimus prime|john cena|anti christ)\b',),
}
COMPILED = {k: tuple(re.compile(p, re.I) for p in v) for k, v in RULES.items()}
# Exact heading aliases are reliable, even when evidence discusses another impact.
ALIASES = {
 'miscalc':'Nuclear Risk','escalation miscalc':'Nuclear Risk','tensions':'Nuclear Risk',
 'b61s':'Nuclear Risk','b61 s':'Nuclear Risk','entanglement':'Nuclear Risk',
 'victim remediation':'Nuclear Justice','victim assistance':'Nuclear Justice','victim assitance':'Nuclear Justice',
 'testing':'Nuclear Justice','humanitarian risks':'Nuclear Justice','reparations':'Nuclear Justice','reperations':'Nuclear Justice','aid':'Nuclear Justice',
 'sv':'Framework','structural violence':'Framework','extinction comes first':'Framework',
 'turkey':'Nonproliferation','poland':'Nonproliferation','east asian prolif':'Nonproliferation',
 'subs':'Nuclear Risk','arctic':'Russia and Ukraine','lithuania':'Russia and Ukraine',
 'london':'UK Influence','united kingdom':'UK Influence','uk':'UK Influence',
 'conventional':'Conventional Arms','modern weapons':'Conventional Arms','insufficient conventional defense':'Conventional Arms',
 'austerity':'Economy','divestment':'Divestment','nuclear investment':'Divestment',
 'collapse':'NATO and Alliances','us pullout':'NATO and Alliances','us relations':'NATO and Alliances',
 'disclosure':'Theory and Topicality','advocacy':'Theory and Topicality',
 'cap sustainable':'Critiques','power rep':'Critiques','animal suffering':'Framework','animal wipeout':'Framework',
 'resilience':'NATO and Alliances','resiliency':'NATO and Alliances',
 'humanitarian law and human rights':'Nuclear Justice','arms control':'Diplomacy',
 'hell':'Speculative Impacts','terror':'Nuclear Risk','hybrid war':'Russia and Ukraine',
 'sudan':'UK Influence','uk influence':'UK Influence','security council':'UK Influence',
 'conventional shift':'Conventional Arms','hypersonics':'Conventional Arms',
 'bioweapons':'Bioweapons','biological weapons':'Bioweapons',
 'deterrence':'Nuclear Deterrence','nuclear deterrence':'Nuclear Deterrence',
 'coercion':'Nuclear Deterrence','conventional deterrence':'Nuclear Deterrence',
 'umbrella':'Nuclear Deterrence','extended deterrence':'Nuclear Deterrence',
 'nato':'NATO and Alliances','alliance cohesion':'NATO and Alliances',
 'disarmament':'Disarmament','nuclear ban':'Disarmament','abolition':'Disarmament',
 'proliferation':'Nonproliferation','nonproliferation':'Nonproliferation','npt':'Nonproliferation',
 'prolif':'Nonproliferation','nonprolif':'Nonproliferation','arms race':'Nonproliferation',
 'tpnw':'Treaty Compliance','verification':'Treaty Compliance','ratification':'Treaty Compliance',
 'nuclear war':'Nuclear Risk','nukes':'Nuclear Risk','nuclear winter':'Nuclear Risk',
 'terrorism':'Nuclear Risk','accidents':'Nuclear Risk','miscalculation':'Nuclear Risk',
 'russia':'Russia and Ukraine','ukraine':'Russia and Ukraine',
 'south korea':'Korea and Japan','north korea':'Korea and Japan','korea':'Korea and Japan','japan':'Korea and Japan',
 'econ':'Economy','economics':'Economy','economy':'Economy','econony':'Economy',
 'grid':'Energy','grids':'Energy','grid collapse':'Energy','grid power':'Energy','energy':'Energy',
 'electricity':'Energy','electricity prices':'Energy','energy prices':'Energy','clean energy':'Energy','renewables':'Energy','nuclear':'Energy','fusion':'Energy','smrs':'Energy',
 'water':'Water','climate':'Climate','warming':'Climate','climate change':'Climate',
 'environment':'Environment','environmental':'Environment','enviorment':'Environment','pollution':'Environment',
 'tech race':'Tech Race','ai race':'Tech Race','leadership':'Tech Race','ai leadership':'Tech Race','race':'Tech Race',
 'bubble':'AI Investment','bubbles':'AI Investment','ai bubble':'AI Investment','investment':'AI Investment',
 'midterms':'Midterms','politics':'Midterms','china':'China and Taiwan','taiwan':'China and Taiwan',
 'diplomacy':'Diplomacy','diplo':'Diplomacy','dipplomacy':'Diplomacy','detente':'Diplomacy',
 'military':'Military and Security','military ai':'Military AI','laws':'Military AI','isr':'Military AI',
 'medical ai':'Healthcare','medical':'Healthcare','medicine':'Healthcare','healthcare':'Healthcare',
 'cyber':'Cybersecurity','cybersecurity':'Cybersecurity','quantum':'Quantum','offshoring':'Offshoring',
 'ai':'AI','agi':'AI Safety','misalignment':'AI Safety','alignment':'AI Safety',
 'regulations':'Regulations','regs':'Regulations','federalism':'Regulations','states':'Regulations',
 'framework':'Framework','fw':'Framework','framing':'Framework','extinction first':'Framework',
 't':'Theory and Topicality','topicality':'Theory and Topicality','theory':'Theory and Topicality',
 'k':'Critiques','space':'Space','agriculture':'Agriculture','democracy':'Democracy',
 'cloud':'AI','semis':'Semiconductors','semiconductors':'Semiconductors',
}
GENERIC = {'','aff','neg','pro','con','cards','card','link','links','off','offs','on','analytics','analytic','extensions','extension','extention','ext','misc','new','other stuff','args','arguments','evidence','random','backlines','backline','2ac','2nc','1ar','1nr','a2','at','adv','da','cp','plan','general','responses','response','overview','ov','o v','u','uq','l','lt','nl','nq','nu','turn','turns','solvency','link defense','impact defense','impact mitigation','defense','impact','case','other unclear','other','top level','core','c1','c2','c3','offense','blocks','rebuttal','frontline'}


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


def heading_topic(value):
    cleaned = clean_heading(value)
    if cleaned in GENERIC or not cleaned:
        return None
    if cleaned in ALIASES:
        return ALIASES[cleaned]
    # Near-misspellings of a known heading ("detterence") join that category.
    if len(cleaned) >= 6:
        import difflib
        close = difflib.get_close_matches(cleaned, [k for k in ALIASES if len(k) >= 6 and abs(len(k) - len(cleaned)) <= 1], n=1, cutoff=0.88)
        if close:
            return ALIASES[close[0]]
    scores = signals(cleaned)
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


def classify(card):
    headings = [card.get('block_heading',card.get('block','')), card.get('arg_heading',card.get('arg',''))]
    for h in headings:
        if len(h.split()) <= 8 and len(h) <= 85:
            topic = heading_topic(h)
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
    scores = Counter({t: 5*min(tag_scores[t],3) + 2*min(selected_scores[t],3) + min(body_scores[t],3) for t in RULES})
    if any(scores[t] >= 5 for t in scores if t != 'AI'):
        scores['AI'] = 0
    ranked = scores.most_common(2)
    best,score = ranked[0]
    for h in headings:
        topic = heading_topic(h)
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
