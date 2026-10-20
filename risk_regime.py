"""Transparent FRED-based US financial-stress regime (v0.6.5).

This is a descriptive, *unvalidated* heuristic, NOT an asset signal, expected
return, probability, position-sizing input, or backtested optimized model.

Scores: 0 = higher stress; 100 = lower stress. Every pillar contributes
score/100 * its published maximum points. Missing a core pillar blocks the
aggregate instead of silently reweighting the remaining components.
"""
from __future__ import annotations

from math import isfinite

MODEL_VERSION = 'US-Stress v0.6.5 · vorläufig 45/40/15'

# The thresholds and weights are transparent candidate policy parameters, NOT
# empirical optima. NFCI partly overlaps VIX and credit risk; weight is small.
FACTORS = {
    'BAMLH0A0HYM2': {'name': 'High-Yield-Kreditspread', 'weight': 0.45,
                      'risk_on': 3.0, 'risk_off': 6.0, 'unit': 'Prozentpunkte'},
    'VIXCLS': {'name': 'VIX · Aktienvolatilität', 'weight': 0.40,
               'risk_on': 16.0, 'risk_off': 32.0, 'unit': 'Indexpunkte'},
    'NFCI': {'name': 'NFCI · Finanzierungsbedingungen', 'weight': 0.15,
             'risk_on': -0.5, 'risk_off': 0.5, 'unit': 'Index'},
}
REQUIRED = frozenset(FACTORS)  # No hidden redistribution of missing pillar weights.
MIN_COVERAGE = 1.0


def _numeric(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def factor_score(value: float, good: float, bad: float) -> float:
    """Clamped linear score; higher input stress -> lower score."""
    v = _numeric(value)
    if v is None or not (bad > good):
        raise ValueError('Ungültiger Faktorwert oder Grenzwerte')
    return round(max(0.0, min(100.0, (bad - v) * 100.0 / (bad - good))), 2)


def assess(records: list[dict] | None) -> dict:
    """Evaluate separate pillars, preserve audit-ready raw point contributions."""
    by_id = {r.get('id'): r for r in (records or []) if isinstance(r, dict)}
    details = []
    weighted_sum = 0.0
    weight_available = 0.0
    missing_core = []
    for sid, cfg in FACTORS.items():
        r = by_id.get(sid, {})
        raw_value = _numeric(r.get('value'))
        status = r.get('status', 'FEHLT')
        valid = status == 'AKTUELL' and raw_value is not None and r.get('date') is not None
        score = factor_score(raw_value, cfg['risk_on'], cfg['risk_off']) if valid else None
        weight_pct = cfg['weight'] * 100.0
        # Display contribution to a 100-point maximum, not a rescaled share.
        points = round(score * cfg['weight'], 4) if valid else None
        if valid:
            weighted_sum += points
            weight_available += cfg['weight']
        else:
            missing_core.append(sid)
        details.append({
            'id': sid, 'name': cfg['name'],
            'value': raw_value, 'unit': cfg['unit'], 'date': r.get('date'),
            'status': status, 'fetch_status': r.get('fetch_status', 'NICHT GEPRÜFT'),
            'checked_at_utc': r.get('checked_at_utc'),
            'score': score, 'weight_pct': weight_pct,
            'points': points, 'max_points': weight_pct,
            'reason': None if valid else (
                'Nicht aktuell' if status == 'VERALTET' else
                'Fehler beim Abruf / ungültige Daten' if status == 'FEHLER' else
                'Messwert, Datum oder Datenreihe fehlt'),
        })

    coverage = round(weight_available * 100.0, 1)
    reliable = not missing_core
    reasons = [] if reliable else [
        'Pflichtsäule fehlt oder ist veraltet: ' + ', '.join(missing_core),
        'Kein Umgewichten oder neutrales Auffüllen fehlender Punkte.',
    ]
    score = round(weighted_sum, 2) if reliable else None
    if score is None:
        label, color = 'NICHT BEWERTBAR', 'gray'
    elif score >= 65.0:
        label, color = 'RISK-ON', 'green'
    elif score <= 35.0:
        label, color = 'RISK-OFF', 'red'
    else:
        label, color = 'NEUTRAL', 'yellow'
    return {
        'label': label, 'color': color, 'score': score,
        'coverage_pct': coverage, 'reliable': reliable,
        'reasons': reasons, 'details': details,
        'model_version': MODEL_VERSION,
        'scored_points': round(weighted_sum, 2),
        'points_possible': 100.0,
        'excluded_context': ('ICSA (Erstanträge) bleibt Makro-Kontext, kein Risk-Score-Faktor.'),
        'explanation': ('Beschreibende US-Finanzmarktstress-Ampel; Gewichte/Grenzen nicht empirisch '
                        'validiert. Kein Long-/Short-Signal und keine automatische Risikoanpassung.'),
    }
