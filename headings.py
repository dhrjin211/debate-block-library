import random
from collections import Counter
from pathlib import Path

from docx import Document

files = [f for f in Path("docs").rglob("*.docx") if not f.name.startswith("~$")]
random.seed(1)
random.shuffle(files)
full = {"Heading 1": Counter(), "Heading 2": Counter(), "Heading 3": Counter()}
first = Counter()
n = 0
for f in files[:600]:
    try:
        paras = Document(f).paragraphs
    except Exception:
        continue
    n += 1
    for p in paras:
        t = p.text.strip()
        if p.style.name in full and t:
            full[p.style.name][t[:60]] += 1
            first[(p.style.name[-1], " ".join(t.lower().split()[:2]))] += 1
print(n, "files sampled")
for k, c in full.items():
    print(f"\n== {k}: most common ==")
    for t, v in c.most_common(30):
        print(v, "|", t)
print("\n== most common opening words (level, words) ==")
for (lvl, w), v in first.most_common(40):
    print(v, "| H" + lvl, "|", w)
