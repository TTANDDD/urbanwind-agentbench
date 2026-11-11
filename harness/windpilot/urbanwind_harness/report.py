"""Report observed coverage and paired original scores without imputing missing runs."""
import argparse
import math
import random
from datetime import datetime
from statistics import mean, median
from pathlib import Path

from .common import load, now, write


def finite_score(item):
    value = item.get('score')
    return item.get('status') == 'evaluated' and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 100


def cost_summary(items):
    result, unknown = {}, {}
    for field in ('wall_seconds', 'tool_cpu_seconds', 'total_local_cpu_seconds_estimate', 'prompt_tokens', 'completion_tokens'):
        values = [(r.get('usage') or {}).get(field) if field.endswith('tokens') else r.get(field) for r in items]
        known = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0]
        name = 'tool_cpu_seconds_estimate' if field == 'tool_cpu_seconds' else field
        result[name] = sum(known) if len(known) == len(items) else None
        unknown[name] = {'missing_attempts':len(items)-len(known), 'known_subtotal':sum(known)}
    result['missing_cost_audit'] = unknown
    result['usage_scope'] = 'Returned provider usage only; failed request usage can be unknown. Token counts are not currency. CPU is a sampling estimate; subcosts are already included.'
    return result


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
    observed = {}
    for r in results:
        key = (r['task'], r['condition'])
        if key not in observed or finite_score(r) or not finite_score(observed[key]):
            observed[key] = r
    evaluated = {key: r for key, r in observed.items() if finite_score(r)}
    missing = [{'task': task, 'condition': condition} for task in tasks for condition in conditions
        if (task, condition) not in evaluated]
    by_condition = {}
    for condition in conditions:
        scored = [evaluated[(task, condition)] for task in tasks if (task, condition) in evaluated]
        count = len(scored)
        full = [r for r in scored if r['score'] == 100 and bool(r.get('full_score', r.get('success')))]
        steps = [r['steps'] for r in scored if isinstance(r.get('steps'), int)]
        full_steps = [r['steps'] for r in full if isinstance(r.get('steps'), int)]
        by_condition[condition] = {'planned_tasks': len(tasks), 'evaluated_tasks': count,
            'mean_score_on_evaluated_tasks': mean(r['score'] for r in scored) if count else None,
            'full_score_tasks': len(full), 'full_score_rate_on_evaluated_tasks': len(full)/count if count else None,
            'mean_actions_on_evaluated_tasks':mean(steps) if steps else None,
            'mean_actions_on_full_score_tasks':mean(full_steps) if full_steps else None,
            'median_actions_on_full_score_tasks':median(full_steps) if full_steps else None,
            'error_free_tasks': sum(bool(r.get('error_free')) for r in scored),
            **cost_summary(scored)}
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
        **cost_summary([r for r in finished_attempts if r['condition'] == condition])}
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
    first = dispatch.get('first_worker_started_at')
    if first:
        last = max(r['finished_at'] for r in evaluated.values()) if not missing else result['generated_at']
        result['queue_elapsed_seconds'] = (datetime.fromisoformat(last)-datetime.fromisoformat(first)).total_seconds()
        result['queue_elapsed_scope'] = 'First worker dispatch to final score if complete, otherwise to this observation. Sum of task wall times is a separate quantity.'
    else:
        result['queue_elapsed_seconds'] = None
    write(run_root/'summary.json', result)
    rows = [f'# {len(tasks)}条任务评测进度与结果', '',
        f"已独立评分 {len(evaluated)}/{len(tasks)*len(conditions)} 次；完整覆盖：{'是' if not missing else '否'}。",
        f"优先报告原评价器100分条数，再报告均分与满分子集动作。无报错不等于隐藏数值吻合。开发或已接触任务{len(protocol['development_tasks'])}条，其余{len(protocol['main_holdout_tasks'])}条单独报告；这不等于完全未知数据。", '',
        '| 任务 | '+' | '.join(conditions)+' |', '|---|'+'---|'*len(conditions)]
    for task in tasks:
        values = []
        for condition in conditions:
            item = observed.get((task, condition))
            values.append(str(item['score']) if item and finite_score(item) else ('用户取消（未知分）' if item and item.get('termination') == 'user_cancelled' else ('基础设施中断' if item else '待完成')))
        rows.append('| '+task+('（开发）' if task in protocol['development_tasks'] else '')+' | '+' | '.join(values)+' |')
    for condition, stats in by_condition.items():
        rows += ['', f"{condition}：满分 {stats['full_score_tasks']}/{stats['evaluated_tasks']}；已评分均分 {stats['mean_score_on_evaluated_tasks']}；满分子集平均动作 {stats['mean_actions_on_full_score_tasks']}（空子集为未知）。"]
    rows += ['', '模型权重固定，每任务单次；尚未全覆盖时不报告最终方法提升。CPU为采样估计；失败请求用量未知，token不是人民币。并行各任务墙钟之和不等于队列实际历时。公开结构通过不等于数值满分。']
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
