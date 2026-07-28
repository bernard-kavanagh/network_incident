"""HTAP analytics over the substrate (TiFlash). Shared by the ADK Deviation
tool and the eval harness so the deviation logic lives in one place."""
from lib.tidb import query


def volume_deviation(subcategory: str, recent_days: int = 3, baseline_days: int = 60) -> dict:
    """Compare recent daily ticket volume for a subcategory against its historical
    daily baseline (mean + stddev) via TiFlash, and return a z-score + verdict.

    Returns: {subcategory, recent_avg_per_day, baseline_mean, baseline_stddev,
              z_score, verdict}.
    """
    sql = """
        SELECT /*+ read_from_storage(tiflash[tickets]) */
               recent.recent_total / %s                       AS recent_avg_per_day,
               base.baseline_mean                              AS baseline_mean,
               base.baseline_stddev                            AS baseline_stddev
        FROM
          (SELECT COUNT(*) AS recent_total FROM tickets
            WHERE subcategory = %s
              AND created_at >= NOW() - INTERVAL %s DAY) recent,
          (SELECT AVG(daily) AS baseline_mean, STDDEV_SAMP(daily) AS baseline_stddev FROM
             (SELECT DATE(created_at) d, COUNT(*) daily FROM tickets
               WHERE subcategory = %s
                 AND created_at <  NOW() - INTERVAL %s DAY
                 AND created_at >= NOW() - INTERVAL %s DAY
               GROUP BY DATE(created_at)) per_day
          ) base
    """
    rows = query(sql, (recent_days, subcategory, recent_days,
                       subcategory, recent_days, baseline_days + recent_days))
    if not rows:
        return {"subcategory": subcategory, "verdict": "no_data"}

    r = rows[0]
    recent = float(r["recent_avg_per_day"] or 0)
    mean = float(r["baseline_mean"] or 0)
    std = float(r["baseline_stddev"] or 0)
    z = round((recent - mean) / std, 2) if std > 0 else None
    if z is None:
        verdict = "insufficient_baseline"
    elif z >= 3:
        verdict = "severe_deviation"
    elif z >= 2:
        verdict = "significant_deviation"
    elif z <= -2:
        verdict = "abnormally_low"
    else:
        verdict = "within_normal_range"
    return {"subcategory": subcategory, "recent_avg_per_day": round(recent, 2),
            "baseline_mean": round(mean, 2), "baseline_stddev": round(std, 2),
            "z_score": z, "verdict": verdict}
