"""Research metadata enrichment: generate a plan, or apply it with backup."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adhesive_ai.compute_parameter_enrichment import BATCH_ID, UNKNOWN_FIELDS, enrich


def build(args) -> None:
    rows = json.loads(Path(args.input).read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "resources/compute_parameter_sources.json").read_text(encoding="utf-8"))
    specific = json.loads(Path(args.specific_model).read_text(encoding="utf-8"))
    entries = [enrich(row, sources, specific) for row in rows]
    if len({x['candidate_id'] for x in entries}) != len(entries):
        raise ValueError("Duplicate candidate IDs")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "plan.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = Counter(key for item in entries for key, value in item['parameters'].items() if value is not None)
    summary = {
        "batch_id": BATCH_ID, "candidates": len(entries),
        "versions": dict(Counter(x['candidate_library_version'] for x in entries)),
        "parameters_with_values": dict(counts),
        "candidates_with_specific_model": sum(x['proposed_model'] is not None for x in entries),
        "candidates_with_reference_routes": sum(bool(x['reference_routes']) for x in entries),
        "production_ready_candidates": 0,
        "policy": "unknown parameters are null; references are not assignments; proxy predictions are not validation",
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "candidate_parameters.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(['candidate_id','formulation_id','library_version','resin','dynamic_unit',*UNKNOWN_FIELDS,'missing_fields','reference_urls','production_approved'])
        for item in entries:
            p=item['parameters']
            values=[json.dumps(p[key],ensure_ascii=False) if isinstance(p[key],(dict,list)) else p[key] for key in UNKNOWN_FIELDS]
            writer.writerow([item['candidate_id'],item['formulation_id'],item['candidate_library_version'],item['resin'],item['dynamic_unit'],*values,';'.join(item['missing_fields']),';'.join(x['url'] for x in item['reference_routes']),False])
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def apply_plan(path: str) -> None:
    from adhesive_ai.database import connection
    entries = json.loads(Path(path).read_text(encoding='utf-8'))
    by_id = {x['candidate_id']: x for x in entries}
    if len(by_id) != len(entries) or any(x['batch_id'] != BATCH_ID for x in entries):
        raise ValueError('Invalid plan identity')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup_dir=ROOT/'work/parameter_enrichment'/stamp
    backup_dir.mkdir(parents=True,exist_ok=False,mode=0o700)
    with connection() as conn:
        cur=conn.cursor(dictionary=True)
        cur.execute('SELECT candidate_id,formulation_id,candidate_library_version,resin,dynamic_unit,properties FROM candidates ORDER BY candidate_id FOR UPDATE')
        rows=cur.fetchall()
        if set(by_id) != {x['candidate_id'] for x in rows}:
            raise ValueError('Candidate inventory changed; rebuild plan')
        updates=[]
        for row in rows:
            entry=by_id[row['candidate_id']]
            for key in ('formulation_id','candidate_library_version','resin','dynamic_unit'):
                if row[key] != entry[key]:
                    raise ValueError('Candidate identity changed: '+row['candidate_id'])
            props=row['properties']
            if isinstance(props,str): props=json.loads(props)
            props=dict(props or {})
            existing=props.get('computation_parameters')
            if existing == entry:
                continue
            if existing is not None:
                raise ValueError('Existing calculation metadata requires merge review: '+row['candidate_id'])
            props['computation_parameters']=entry
            updates.append((json.dumps(props,ensure_ascii=False),row['candidate_id'],row['formulation_id']))
        backup=backup_dir/'candidates-before.json'
        backup.write_text(json.dumps(rows,ensure_ascii=False,default=str,indent=2),encoding='utf-8')
        backup.chmod(0o600)
        if updates:
            cur.executemany('UPDATE candidates SET properties=%s WHERE candidate_id=%s AND formulation_id=%s',updates)
            if cur.rowcount != len(updates):
                conn.rollback()
                raise RuntimeError('Unexpected update count; transaction rolled back')
        cur.close()
    print(json.dumps({'updated':len(updates),'backup':str(backup),'batch_id':BATCH_ID}))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input')
    parser.add_argument('--specific-model',default='work/md_baselines/cl5-00016-badcy-dcdds-ctbn-v1/structure_contract.json')
    parser.add_argument('--output',default='work/parameter_enrichment/research-20261029')
    parser.add_argument('--apply-plan')
    args=parser.parse_args()
    if args.apply_plan: apply_plan(args.apply_plan)
    elif args.input: build(args)
    else: parser.error('provide --input or --apply-plan')
