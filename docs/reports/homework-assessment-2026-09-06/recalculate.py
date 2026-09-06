"""Recalculate metrics locally from evaluation.json and saved ai-reviews."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def summarize(rows):
    binary = [r for r in rows if r['reference_class'] != 'medium']
    tp = sum(r['reference_class'] == 'weak' and r['prediction'] == 'fail' for r in binary)
    fp = sum(r['reference_class'] == 'normal' and r['prediction'] == 'fail' for r in binary)
    positives = sum(r['reference_class'] == 'weak' for r in binary)
    matches = sum(r['match'] for r in binary)
    resolved = sum(r['prediction'] != 'abstain' for r in binary)
    ratio = lambda a, b: a / b if b else None
    return {
        'pairs': len(rows), 'binary_pairs': len(binary),
        'medium_pairs': len(rows) - len(binary), 'matches': matches,
        'agreement': ratio(matches, len(binary)),
        'precision_weak': ratio(tp, tp + fp), 'recall_weak': ratio(tp, positives),
        'f1_weak': ratio(2 * tp, 2 * tp + fp + positives - tp),
        'coverage': ratio(resolved, len(binary)), 'abstain': len(binary) - resolved,
        'tp': tp, 'fp': fp,
        'matrix': {label: dict(Counter(r['prediction'] for r in binary
                                      if r['reference_class'] == label))
                   for label in ['weak', 'normal']},
    }


def main():
    evaluation = json.loads((ROOT / 'evaluation.json').read_text())
    policy = evaluation['missing_score_policy']
    if policy not in {'full_credit', 'exclude'}:
        raise ValueError('Unsupported missing_score_policy')
    rows = []
    for case in evaluation['cases']:
        data = (ROOT / case['review_file']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == case['review_sha256'], case['pair_id']
        review = json.loads(data)
        assert review['id'] == case['run_id'] and review['status'] == 'succeeded'
        suggestions = review['result']['suggestions']
        by_id = {s['criterion_id']: s for s in suggestions}
        assert len(by_id) == len(suggestions)
        assert set(by_id) == {c['id'] for c in case['criteria']}
        task = evaluation['tasks'][case['task_id']]
        score = maximum = 0
        missing = 0
        minimum_fail = False
        for criterion in case['criteria']:
            points = by_id[criterion['id']]['proposed_points']
            if points is None:
                missing += 1
                if policy == 'exclude':
                    continue
                points = criterion['max_points']
            assert 0 <= points <= criterion['max_points']
            score += points
            maximum += criterion['max_points']
            minimum_fail |= points < task['minimums'].get(criterion['key'], 0)
        threshold = task['pass_score'] / task['max_score'] * maximum
        prediction = ('abstain' if maximum == 0 else
                      'fail' if minimum_fail or score + 1e-9 < threshold else 'pass')
        expected = {'weak': 'fail', 'normal': 'pass', 'medium': None}[case['reference_class']]
        rows.append({
            'pair_id': case['pair_id'], 'task_id': case['task_id'],
            'unit_id': case['unit_id'], 'run_id': case['run_id'],
            'reference_class': case['reference_class'],
            'score': score, 'max_score': maximum, 'pass_score': threshold,
            'criteria_without_score': missing, 'prediction': prediction,
            'match': prediction == expected if expected is not None else None,
            'review_file': case['review_file'],
        })
    metrics = summarize(rows)
    metrics['by_task'] = {
        task: summarize([r for r in rows if r['task_id'] == task])
        for task in evaluation['tasks']
    }
    runs = json.loads((ROOT / 'telemetry.json').read_text())['runs']
    assert {r['run_id'] for r in runs} == {r['run_id'] for r in rows}
    metrics['telemetry'] = {
        'runs': len(runs), 'criterion_rows': sum(len(c['criteria']) for c in evaluation['cases']),
        'models': sorted({r['model'] for r in runs}),
        **{key: sum(r['ledger'][key] for r in runs)
           for key in ['calls', 'prompt_tokens', 'completion_tokens', 'cache_hit_tokens']},
        'estimated_cost_usd': round(sum(r['ledger']['cost_usd'] for r in runs), 5),
    }
    (ROOT / 'metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + '\n')
    with (ROOT / 'results.csv').open('w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({key: value for key, value in metrics.items() if key != 'by_task'},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
