"""Audit every retained source card and export reproducible placement/veto decisions.

  python audit_cards.py /path/to/report-directory
Source database rows remain available; vetoes hide invalid records from browsing.
"""
import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from cardgrab import DB, label
from organize import classify, display_title, veto


def audit(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
    rows=[dict(r) for r in con.execute('SELECT rowid id,* FROM cards')];con.close()
    records=[];counts=Counter();topics=Counter()
    for row in rows:
        raw={**row,'arg':row['arg_heading'],'block':row['block_heading']}
        issue=veto(raw)
        placement=label(raw,{},False,row['source_file'])
        topic,reason,review=classify(raw)
        decision='vetoed' if issue or not placement else 'needs review' if review else 'organized'
        counts[decision]+=1
        if placement:topics[topic]+=1
        records.append(dict(id=row['id'],decision=decision,old_topic=row['arg'],new_topic=topic,
                            response_type=placement[2] if placement else '',reason=issue or reason,
                            title=display_title(row['tag']),source_tag=row['tag'],source_file=row['source_file'],
                            arg_heading=row['arg_heading'],block_heading=row['block_heading']))
    with (out/'Cardgrab-card-audit.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=records[0].keys());writer.writeheader();writer.writerows(records)
    summary=dict(source_cards=len(rows),decisions=dict(counts),topics=dict(topics.most_common()),
                 max_topic_length=max(map(len,topics)),heading_prose_rejected=sum(len(r['arg_heading'])>140 or len(r['block_heading'])>140 for r in rows))
    (out/'Cardgrab-audit-summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))
    return summary


if __name__=='__main__':audit(sys.argv[1] if len(sys.argv)>1 else 'audit')
