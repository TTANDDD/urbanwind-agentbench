"""Report observed coverage and paired original scores without imputing missing runs."""
import argparse
import random
from statistics import mean
from pathlib import Path

from .common import load, now, write


def paired_difference(pairs):
    if not pairs:
        return {'pairs': 0, 'mean_score_difference': None}
    deltas = [right['score']-left['score'] for left, right in pairs]
    rng = random.Random(20261004)
    boot = sorted(mean(rng.choices(deltas, k=len(deltas))) for _ in range(5000))
    return {'pairs': len(pairs), 'mean_score_difference': mean(deltas),
        'descriptive_task_bootstrap_95_interval': [boot[124], boot[4874]],
        'wins': sum(x > 0 for x in deltas), 'ties': sum(x == 0 for x in deltas),
        'losses': sum(x < 0 for x in deltas),
        'limitation': 'One replicate; shared task families are not independent cities. This interval does not establish generalization.'}


def summarize(run_root, protocol):
    dispatch = load(run_root/'dispatch.json')
    results = load(run_root/'results.json') if (run_root/'results.json').is_file() else []
    tasks = [entry['task'] for entry in protocol['budgets']]
    conditions = dispatch['conditions']
    observed = {(r['task'], r['condition']): r for r in results}
    evaluated = {key: r for key, r in observed.items() if r['status'] == 'evaluated'}
    missing = [{'task': task, 'condition': condition} for task in tasks for condition in conditions
        if (task, condition) not in evaluated]
    by_condition = {}
    for condition in conditions:
        scored = [evaluated[(task, condition)] for task in tasks if (task, condition) in evaluated]
        count = len(scored)
        by_condition[condition] = {'planned_tasks': len(tasks), 'evaluated_tasks': count,
            'mean_score_on_evaluated_tasks': mean(r['score'] for r in scored) if count else None,
            'full_score_tasks': sum(bool(r.get('full_score', r.get('success'))) for r in scored),
            'error_free_tasks': sum(bool(r.get('error_free')) for r in scored),
            'wall_seconds': sum(r.get('wall_seconds', 0) for r in scored),
            'tool_cpu_seconds_estimate': sum(r.get('tool_cpu_seconds', 0) for r in scored),
            'total_local_cpu_seconds_estimate': sum(r.get('total_local_cpu_seconds_estimate', 0) for r in scored),
            'prompt_tokens': sum(r.get('usage', {}).get('prompt_tokens', 0) for r in scored),
            'completion_tokens': sum(r.get('usage', {}).get('completion_tokens', 0) for r in scored)}
    paired = {}
    for left in conditions:
        if left == 'full' or 'full' not in conditions:
            continue
        for scope, selected in [('all30', tasks), (protocol.get('comparison_scope_name', 'holdout29'), protocol['main_holdout_tasks'])]:
            pairs = [(evaluated[(task, left)], evaluated[(task, 'full')]) for task in selected
                if (task, left) in evaluated and (task, 'full') in evaluated]
            paired[left+'_to_full_'+scope] = paired_difference(pairs)
    finished_attempts = []
    for path in sorted(run_root.glob('*/result.json')):
        config_path = path.parent/'config.json'
        if not config_path.is_file() or load(config_path).get('protocol_sha256') != dispatch['protocol_sha256']:
            continue
        item = load(path)
        if item.get('task') in tasks and item.get('condition') in conditions:
            finished_attempts.append(item)
    attempt_costs = {condition: {'finished_attempts': sum(r['condition'] == condition for r in finished_attempts),
        'infrastructure_incomplete_attempts': sum(r['condition'] == condition and r['status'] == 'infra_incomplete' for r in finished_attempts),
        'wall_seconds': sum(r.get('wall_seconds', 0) for r in finished_attempts if r['condition'] == condition),
        'tool_cpu_seconds_estimate': sum(r.get('tool_cpu_seconds', 0) for r in finished_attempts if r['condition'] == condition),
        'total_local_cpu_seconds_estimate': sum(r.get('total_local_cpu_seconds_estimate', 0) for r in finished_attempts if r['condition'] == condition),
        'prompt_tokens': sum(r.get('usage', {}).get('prompt_tokens', 0) for r in finished_attempts if r['condition'] == condition),
        'completion_tokens': sum(r.get('usage', {}).get('completion_tokens', 0) for r in finished_attempts if r['condition'] == condition)}
        for condition in conditions}
    result = {'generated_at': now(), 'protocol_sha256': dispatch['protocol_sha256'],
        'planned_runs': len(tasks)*len(conditions), 'evaluated_runs': len(evaluated),
        'complete': not missing, 'missing_or_infrastructure_incomplete': missing,
        'conditions': by_condition, 'paired_scores': paired,
        'all_finished_attempt_costs_including_infrastructure_retries': attempt_costs,
        'metrics': protocol['metrics'], 'development_tasks': protocol['development_tasks'],
        'claim_boundary': protocol.get('claim_boundary', 'Original frozen evaluator scores only; partial coverage is not a final 30-task comparison.'),
        'all_tasks': [{'task': task, 'development': task in protocol['development_tasks'],
            'conditions': {condition: observed.get((task, condition)) for condition in conditions}} for task in tasks]}
    write(run_root/'summary.json', result)
    rows = ['# 30条任务评测进度与结果', '',
        f"已独立评分 {len(evaluated)}/{len(tasks)*len(conditions)} 次；完整覆盖：{'是' if not missing else '否'}。",
        f"原评价器连续分数为主指标。无报错不等于隐藏数值吻合。开发或已接触任务{len(protocol['development_tasks'])}条，其余{len(protocol['main_holdout_tasks'])}条单独报告；这不等于完全未知数据。", '',
        '| 任务 | '+' | '.join(conditions)+' |', '|---|'+'---|'*len(conditions)]
    for task in tasks:
        values = []
        for condition in conditions:
            item = observed.get((task, condition))
            values.append(str(item['score']) if item and item['status'] == 'evaluated' else ('基础设施中断' if item else '待完成'))
        rows.append('| '+task+('（开发）' if task in protocol['development_tasks'] else '')+' | '+' | '.join(values)+' |')
    rows += ['', '模型权重固定，单次重复；尚未全覆盖时不报告最终方法提升。CPU成本为进程采样估计，保留完整原始执行记录。']
    (run_root/'结果.md').write_text('\n'.join(rows)+'\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.run_root, load(args.protocol))
    print({key: result[key] for key in ('planned_runs', 'evaluated_runs', 'complete')})


if __name__ == '__main__':
    main()
