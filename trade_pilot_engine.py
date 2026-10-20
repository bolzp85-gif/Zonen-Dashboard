"""Trade-Pilot v0.6.1 – kostenfreie Brutto-Planung für CFDs.

WICHTIG: Spread, Kommission, Finanzierung, Slippage und weitere Brokerkosten
werden absichtlich NICHT modelliert. Brutto-Risiko und -CRV sind deswegen
KEINE Nettoergebnisse und KEINE Verlustgarantie. Reine Planungsrechnung;
keine Brokerorders, keine Erfolgswahrscheinlichkeiten.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
import math


def finite_positive(n: float) -> bool:
    try:
        return math.isfinite(float(n)) and float(n) > 0
    except (ValueError, TypeError, OverflowError):
        return False


def floor_step(n: float, step: float) -> float:
    if not finite_positive(step) or not math.isfinite(n) or n < 0:
        raise ValueError('Ungültige Größe oder Einheitsschritt')
    q = Decimal(str(n)) / Decimal(str(step))
    return float(q.to_integral_value(rounding=ROUND_FLOOR) * Decimal(str(step)))


@dataclass(frozen=True)
class Plan:
    direction: str
    entry: float
    stop: float
    target: float
    atr: float
    equity_eur: float
    free_margin_eur: float
    base_risk_pct: float
    daily_limit_pct: float
    realized_loss_eur: float
    other_open_risk_eur: float
    value_per_point_native: float
    eur_per_native: float
    leverage: float
    min_units: float
    unit_step: float
    minimum_rrr: float
    broker_specs_confirmed: bool
    news_checked: bool
    market_data_current: bool
    is_demo: bool
    high_stress: bool
    higher_tf_conflict: bool


def evaluate(p: Plan) -> dict:
    """Return a *cost-excluded* RRR and (if broker specs verified) max units.

    The geometric RRR is still shown when broker specifications are unknown.
    Without verified instrument sizing, NO position size is calculated.
    """
    blockers: list[str] = []
    warnings: list[str] = []
    numerical = ('entry','stop','target','atr','equity_eur','free_margin_eur',
                 'base_risk_pct','daily_limit_pct','realized_loss_eur','other_open_risk_eur',
                 'value_per_point_native','eur_per_native','leverage','min_units','unit_step',
                 'minimum_rrr')
    try:
        if any(not math.isfinite(float(getattr(p, k))) for k in numerical):
            raise ValueError('non finite input')
    except (TypeError, ValueError, OverflowError):
        return {'units':0.0,'gross_rrr':None,'blockers':['Ungültige oder unendliche Eingabe'],
                'warnings':[],'status':'GESPERRT','costs_included':False}

    if p.direction not in ('Long','Short'):
        blockers.append('Ungültige Handelsrichtung')
    geometry_ok = ((p.direction == 'Long' and 0 < p.stop < p.entry < p.target)
                   or (p.direction == 'Short' and 0 < p.target < p.entry < p.stop))
    if not geometry_ok:
        blockers.append('Ungültige Preisstruktur: Entry, Stop und Ziel prüfen')
    if not finite_positive(p.equity_eur):
        blockers.append('Kontostand / Equity muss positiv sein')
    if any(getattr(p, k) < 0 for k in
           ('atr','free_margin_eur','base_risk_pct','daily_limit_pct',
            'realized_loss_eur','other_open_risk_eur')):
        blockers.append('Negative Risiko-, Kontodaten- oder ATR-Werte sind unzulässig')
    if p.base_risk_pct > 2 or p.daily_limit_pct > 5 or not finite_positive(p.minimum_rrr):
        blockers.append('Risikoparameter außerhalb der Sicherheitsgrenzen')
    if not p.broker_specs_confirmed:
        blockers.append('Punktwert, Währungsumrechnung und Margin-/Einheitendaten nicht bestätigt')
    if not p.news_checked:
        blockers.append('High-Impact-News im nächsten 30-Minuten-Fenster nicht geprüft')
    if not p.market_data_current:
        blockers.append('OHLC-Marktdaten fehlen oder sind verzögert')
    if p.is_demo:
        blockers.append('Demo-Kurse: keine reale Handelsplanung')
    if p.high_stress:
        blockers.append('Hohe Belastung: Handelsplanung gesperrt')
    if p.higher_tf_conflict:
        blockers.append('Trade widerspricht bestätigtem 4H-Trend')
    if p.atr <= 0:
        warnings.append('ATR fehlt: volatilitätsbasierter Abstand nicht bewertbar')
    if not geometry_ok:
        return {'units':0.0,'gross_rrr':None,'status':'GESPERRT',
                'blockers':blockers,'warnings':warnings,'costs_included':False}

    stop_points = abs(p.entry - p.stop)
    reward_points = abs(p.target - p.entry)
    gross_rrr = reward_points / stop_points
    if gross_rrr < p.minimum_rrr:
        blockers.append(f'Brutto-CRV {gross_rrr:.2f} < Mindest-CRV {p.minimum_rrr:.2f}')
    if p.atr > 0 and (stop_points / p.atr < .5 or stop_points / p.atr > 4.):
        warnings.append(f'Stop-Abstand {stop_points/p.atr:.2f} ATR; mit Marktstruktur abgleichen')

    base = {'gross_rrr':gross_rrr,'stop_points':stop_points,'reward_points':reward_points,
            'stop_atr':stop_points / p.atr if p.atr > 0 else None,
            'target_atr':reward_points / p.atr if p.atr > 0 else None,
            'costs_included':False,'warnings':warnings}

    # Stop early: apparent unit counts based on the placeholder 1 EUR/point are dangerous.
    confirmed_params = (p.broker_specs_confirmed and
                        all(finite_positive(getattr(p,k)) for k in
                            ('value_per_point_native','eur_per_native','leverage','min_units','unit_step')) and
                        p.leverage >= 1)
    if not confirmed_params or not finite_positive(p.equity_eur) or p.free_margin_eur < 0:
        if p.broker_specs_confirmed and not confirmed_params:
            blockers.append('Ungültige Broker-/Kontraktparameter')
        return {**base,'units':0.0,'status':'GESPERRT',
                'blockers':blockers,'risk_budget_eur':None,'daily_remaining_eur':None}

    point_eur = p.value_per_point_native * p.eur_per_native
    loss_per_unit = stop_points * point_eur
    reward_per_unit = reward_points * point_eur
    nominal_per_unit = p.entry * point_eur
    margin_per_unit = nominal_per_unit / p.leverage
    if not finite_positive(loss_per_unit) or not finite_positive(margin_per_unit):
        return {**base,'units':0.0,'status':'GESPERRT',
                'blockers':blockers+['Risikowert oder Margin nicht berechenbar']}

    day_limit = p.equity_eur * p.daily_limit_pct / 100
    day_remaining = max(0.0, day_limit - p.realized_loss_eur - p.other_open_risk_eur)
    trade_budget = min(p.equity_eur * p.base_risk_pct / 100, day_remaining)
    if day_remaining <= 0:
        blockers.append('Tages-Risikobudget aufgebraucht')
    if p.free_margin_eur <= 0:
        blockers.append('Keine freie Margin')
    risk_units = floor_step(trade_budget / loss_per_unit, p.unit_step)
    margin_units = floor_step(p.free_margin_eur / margin_per_unit, p.unit_step)
    units = min(risk_units, margin_units)
    if units + 1e-10 < p.min_units:
        units = 0.0
        blockers.append('Keine handelbare Positionsgröße innerhalb beider Limits')
    elif units > 0:
        if units * loss_per_unit > trade_budget + 1e-7 or units * margin_per_unit > p.free_margin_eur + 1e-7:
            raise AssertionError('Position überschreitet die geplanten Limits')
    # Blocked trades have NO proposed tradable quantity (even if the sizing maths would fit).
    units = units if not blockers else 0.0
    return {**base,
        'status':'PLANUNG REGELKONFORM' if not blockers else 'GESPERRT',
        'units':units,
        'max_risk_units':risk_units,
        'max_margin_units':margin_units,
        'limit_reason':'Risikobudget' if risk_units < margin_units else ('Freie Margin' if margin_units < risk_units else 'Beide Limits'),
        'risk_budget_eur':trade_budget,
        'daily_remaining_eur':day_remaining,
        'stop_risk_eur':units*loss_per_unit,
        'gross_reward_eur':units*reward_per_unit,
        'margin_eur':units*margin_per_unit,
        'nominal_eur':units*nominal_per_unit,
        'blockers':blockers,'warnings':warnings,
    }
