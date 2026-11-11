"""Recompute paper summary statistics from saved per-task records; no model calls."""
import json, statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
data=json.loads((ROOT/'paper/data/per_task_merged.json').read_text(encoding='utf-8'))
summary={'task_count':len(data),'source':'saved development-run records','conditions':{}}
for label,key in (('base_agent','base'),('windpilot','new')):
    scores=[v[key]['score_corrected'] if key=='base' else v[key]['score'] for v in data.values()]
    summary['conditions'][label]={'mean_score':statistics.mean(scores),'numerical_credit':sum(s>60 for s in scores),
        'full_score':sum(s==100 for s in scores),'at_least_structural_maximum':sum(s>=60 for s in scores),
        'mean_steps':statistics.mean(v[key]['steps'] for v in data.values()),
        'mean_tokens':statistics.mean(v[key]['tokens'] for v in data.values())}
base,new=summary['conditions'].values()
summary['mean_score_gain']=new['mean_score']-base['mean_score']
summary['token_reduction_percent']=100*(1-new['mean_tokens']/base['mean_tokens'])
abl=[v for t,v in data.items() if 61<=int(t[5:])<=80]
summary['abl_task_count']=len(abl)
summary['abl_mean_score_gain']=statistics.mean(v['new']['score']-v['base']['score_corrected'] for v in abl)
assert len(data)==30 and len(abl)==13
assert (base['numerical_credit'],new['numerical_credit'],base['full_score'],new['full_score'])==(6,17,0,3)
assert round(base['mean_score'],1)==62.5 and round(new['mean_score'],1)==72.5
print(json.dumps(summary,indent=2))
