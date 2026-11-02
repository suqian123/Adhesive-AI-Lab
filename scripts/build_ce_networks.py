"""Build the three candidate-specific chemical connectivity prototypes."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from adhesive_ai.ce_network import prepare_networks

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='work/md_baselines/cl5-00016-badcy-dcdds-ctbn-v1/networks')
    args=parser.parse_args()
    for report in prepare_networks(args.output):
        print(json.dumps({k:v for k,v in report.items() if k not in ('reaction_atom_mapping','artifact_sha256')},ensure_ascii=False))
