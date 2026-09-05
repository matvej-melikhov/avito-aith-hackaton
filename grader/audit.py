#!/usr/bin/env python3
"""Аудит смещений: не оценил ли пайплайн объём вместо содержания."""
def spearman(a, b):
    n = len(a)
    if n < 2: return 0.0
    ra = {v: i for i, v in enumerate(sorted(a))}
    rb = {v: i for i, v in enumerate(sorted(b))}
    d2 = sum((ra[x] - rb[y]) ** 2 for x, y in zip(a, b))
    return 1 - 6 * d2 / (n * (n * n - 1))

def length_bias(totals, lengths):
    """totals/lengths: {sid: value}. Возвращает предупреждение, если ранжирование
    совпало с ранжированием по объёму."""
    sids = sorted(totals)
    by_score = sorted(sids, key=lambda s: -totals[s])
    by_len   = sorted(sids, key=lambda s: -lengths[s])
    rho = spearman([totals[s] for s in sids], [lengths[s] for s in sids])
    return {
        "rank_by_score": by_score,
        "rank_by_length": by_len,
        "identical_to_length_ranking": by_score == by_len,
        "spearman_score_vs_length": round(rho, 2),
        "warning": ("Ранжирование совпало с ранжированием по объёму — проверь, "
                    "что баллы опираются на цитаты, а не на многословие.")
                   if by_score == by_len and len(sids) > 2 else None,
    }

def position_agreement(pass1, pass2):
    """Согласие двух прогонов с разным порядком подачи работ."""
    return {"pass1": pass1, "pass2": pass2, "stable": pass1 == pass2,
            "warning": None if pass1 == pass2 else
                       "Порядок подачи изменил вердикт — работы близки, нужен человек."}
