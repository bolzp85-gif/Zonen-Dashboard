"""FRED-based US risk-appetite *context* indicator; NOT an entry/exit signal.

Inspired by the coverage-aware weighted pillar philosophy of the author's
separate multi-asset regime dashboard.  This deliberately DOES NOT reuse its
asset-specific bull/bear bias or position-size suggestions: this module is a
lightweight market-wide US financial-stress proxy, not an asset forecast.

Scores are illustrative policy heuristics; they are not backtested probabilities.
"""
from __future__ import annotations

from math import isfinite

# Fixed, transparent starting weights; do not optimize without an out-of-sample audit.
FACTORS = {
    'BAMLH0A0HYM2': {'name': 'High-Yield-Kreditspread', 'weight': 0.35,
                      'risk_on': 3.0, 'risk_off': 6.0, 'unit': '%-Punkte'},
    'VIXCLS': {'name': 'VIX – erwartete Aktienvolatilität', 'weight': 0.30,
               'risk_on': 16.0, 'risk_off': 32.0, 'unit': 'Indexpunkte'},
    'NFCI': {'name': 'Nationale Finanzierungsbedingungen', 'weight': 0.20,
             'risk_on': -0.5, 'risk_off': 0.5, 'unit': 'Index'},
    'ICSA': {'name': 'US-Erstanträge – Änderung 4W Ø', 'weight': 0.15,
             'risk_on': -5.0, 'risk_off': 12.0, 'unit': '%'},
}
REQUIRED = frozenset({'BAMLH0A0HYM2', 'VIXCLS'})
MIN_COVERAGE = 0.80


def _numeric(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def factor_score(value: float, good: float, bad: float) -> float:
    """0=high stress, 100=low stress. Fixed, capped linear map."""
    v = _numeric(value)
    if v is None or not (bad > good):
        raise ValueError('Ungültiger Faktorwert oder Grenzwerte')
    return round(max(0.0, min(100.0, (bad - v) * 100.0 / (bad - good))), 2)


def assess(records: list[dict] | None) -> dict:
    """Never manufacture a neutral regime from stale, missing or partial data.

    Requires FRED high-yield spread + VIX and >=80% of configured pillar weight
    to be current.  No market/order permissions and no trading instructions.
    """
    by_id = {r.get('id'): r for r in (records or []) if isinstance(r, dict)}
    details = []
    weighted_sum = 0.0
    weight_available = 0.0
    for sid, cfg in FACTORS.items():
        r = by_id.get(sid, {})
        raw = r.get('change_4w_pct') if sid == 'ICSA' else r.get('value')
        value = _numeric(raw) if r.get('status') == 'AKTUELL' else None
        valid = value is not None and r.get('date') is not None
        score = factor_score(value, cfg['risk_on'], cfg['risk_off']) if valid else None
        if score is not None:
            weighted_sum += score * cfg['weight']
            weight_available += cfg['weight']
        details.append({
            'id': sid, 'name': cfg['name'], 'value': value,
            'unit': cfg['unit'], 'date': r.get('date'),
            'status': 'AKTUELL' if valid else r.get('status', 'FEHLT'),
            'score': score, 'weight_pct': int(round(100 * cfg['weight'])),
            'reason': None if valid else (
                'Keine ausreichende 4-Wochen-Historie' if sid == 'ICSA' and r.get('status') == 'AKTUELL'
                else 'Fehlend oder veraltet'),
        })
    coverage = round(weight_available * 100.0, 1)
    present_ids = {x['id'] for x in details if x['score'] is not None}
    missing_core = sorted(REQUIRED - present_ids)
    reasons = []
    if missing_core:
        reasons.append('Pflichtreihen nicht aktuell: ' + ', '.join(missing_core))
    if weight_available < MIN_COVERAGE - 1e-9:
        reasons.append(f'Datenabdeckung {coverage:.1f}% unter {MIN_COVERAGE:.0%}')
    reliable = not reasons
    score = round(weighted_sum / weight_available, 1) if reliable else None
    if score is None:
        label, color = 'NICHT BEWERTBAR', 'gray'
    elif score >= 65.0:
        label, color = 'RISK-ON', 'green'
    elif score <= 35.0:
        label, color = 'RISK-OFF', 'red'
    else:
        label, color = 'NEUTRAL', 'yellow'
    return {
        'label': label, 'color': color, 'score': score, 'coverage_pct': coverage,
        'reliable': reliable, 'reasons': reasons, 'details': details,
        'explanation': ('US-Finanzmarktstress als übergeordnetes Umfeld. '
                        'Kein Long-/Short-Signal, keine Trefferquote und keine automatische Risikoanpassung.'),
    }
