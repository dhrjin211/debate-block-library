"""Check every indexed card against its original Word source.

  python audit_formatting.py audit
"""
import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from cardgrab import DB
from cardformat import resolve


def audit_sources(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
    rows=[dict(r) for r in con.execute('SELECT rowid id,* FROM cards ORDER BY source_file,rowid')]
    con.close()
    counts=Counter();files=set()
    with (out/'Cardgrab-source-format-audit.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['id','status','source_file','reason']);writer.writeheader()
        for index,card in enumerate(rows,1):
            files.add(card['source_file'])
            try:
                resolve(card);status='matched';reason='Exact original Word occurrence recovered'
            except Exception as error:
                status='needs source review';reason=str(error)
            counts[status]+=1
            writer.writerow(dict(id=card['id'],status=status,source_file=card['source_file'],reason=reason))
            if index%500==0:print(f'{index}/{len(rows)} cards checked: {dict(counts)}',flush=True)
    summary=dict(cards=len(rows),source_files=len(files),counts=dict(counts))
    (out/'Cardgrab-source-format-summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))
    return summary


if __name__=='__main__':audit_sources(sys.argv[1] if len(sys.argv)>1 else 'audit')
