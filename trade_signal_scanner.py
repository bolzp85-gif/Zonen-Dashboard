"""Conservative candidate finder for pre-confirmed eToro S/R levels, no broker orders.

Ranks *technical scenarios*; only a confirmed closed 15m reaction merits the
technical trigger label. No account balance, sizing, probabilities or fees here.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from typing import Any
import pandas as pd
from atr_target_context import assess as assess_atr_target

@dataclass(frozen=True)
class Candidate:
    direction: str
    timeframe: str
    entry: float
    stop: float
    target: float
    rrr: float
    atr: float
    stop_atr: float
    target_atr: float
    quality: float
    zone_low: float
    zone_high: float
    price_distance_atr: float
    state: str
    reason: str
    target_context: str
    horizon_bars: int
    reference_close: float
    signal_time_utc: str


def _float(x: Any) -> float | None:
    try:
        a = float(x)
        return a if isfinite(a) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _active_zones(zones: pd.DataFrame | None, timeframe: str) -> pd.DataFrame:
    required = {'Zeitebene', 'Status', 'Seite', 'Untergrenze', 'Obergrenze'}
    if not isinstance(zones, pd.DataFrame) or not required.issubset(zones.columns):
        return pd.DataFrame()
    part = zones.loc[(zones.Zeitebene == timeframe) & (zones.Status != 'Gebrochen')].copy()
    return part


def _confirmed_reaction(frame: pd.DataFrame, *, direction: str,
                        zone_low: float, zone_high: float, atr: float) -> bool:
    """The newest *completed* 15m candle must touch/reclaim a S/R zone.

    This uses neither future candles nor inferred intrabar order flow.
    """
    if not isinstance(frame, pd.DataFrame) or len(frame) < 2:
        return False
    last = frame.iloc[-1]
    c, o, lo, hi = [_float(last[k]) for k in ('close', 'open', 'low', 'high')]
    if any(v is None for v in (c, o, lo, hi)):
        return False
    if direction == 'Long':
        return lo <= zone_high + .10 * atr and c >= zone_high + .05 * atr and c > o
    return hi >= zone_low - .10 * atr and c <= zone_low - .05 * atr and c < o


def scan(*, frames: dict, zones: pd.DataFrame | None, trend_context: dict,
         minimum_rrr: float = 1.5, horizon_bars: int = 8,
         is_demo: bool = False, stale: bool = False,
         quality_floor: float = 0.0) -> dict:
    """Find trend-aligned S/R pullback scenarios and separately label a closed-candle trigger.

    Only considers 1H and 15Min context zones; DOES NOT invent a resistance
    or support to satisfy R:R, nor recommend any entry at current price absent
    a closed 15m reaction.
    """
    if not isfinite(float(minimum_rrr)) or not .5 <= minimum_rrr <= 10:
        raise ValueError('Mindest-CRV außerhalb 0,5–10')
    if not isinstance(horizon_bars, int) or not 1 <= horizon_bars <= 96:
        raise ValueError('Horizont muss 1–96 Kerzen betragen')
    base = {'candidates': [], 'evaluated': 0, 'rejected_crv': 0, 'reason': None,
            'market_data_current': not (stale or is_demo)}
    if not trend_context.get('complete'):
        return {**base, 'reason':'Trendstruktur unvollständig – keine automatische Einstiegsbewertung.'}
    direction = trend_context.get('bias')
    if direction not in ('Long', 'Short'):
        return {**base, 'reason':'4H und 1H bestätigen keine identische Richtung – kein erzwungenes Signal.'}
    if is_demo or stale or not trend_context.get('current'):
        return {**base, 'reason':'Demodaten oder verzögerte Kurse – keine aktuellen technischen Signale.'}
    if '15Min' not in frames or '5Min' not in frames:
        return {**base, 'reason':'15Min-/5Min-Kerzen fehlen.'}
    tf15 = trend_context['frames']['15Min'].trend
    if tf15 == ('Abwärtstrend' if direction == 'Long' else 'Aufwärtstrend'):
        return {**base, 'reason':'15-Minuten-Trend widerspricht dem übergeordneten Trend – Einstieg abwarten.'}
    close = _float(frames['15Min'].close.iloc[-1])
    if close is None or close <= 0:
        return {**base, 'reason':'Kein gültiger abgeschlossener 15-Minuten-Schlusskurs.'}
    expected = 'Aufwärtstrend' if direction == 'Long' else 'Abwärtstrend'
    tf5 = trend_context['frames']['5Min'].trend
    results = []
    for timeframe in ('1H', '15Min'):
        if timeframe not in frames:
            continue
        raw_atr = _float(__import__('engine').atr(frames[timeframe]).iloc[-1])
        if raw_atr is None or raw_atr <= 0:
            continue
        active = _active_zones(zones, timeframe)
        if active.empty:
            continue
        support_side = 'Unterstützung' if direction == 'Long' else 'Widerstand'
        obstacle_side = 'Widerstand' if direction == 'Long' else 'Unterstützung'
        source = active.loc[active.Seite == support_side]
        obstacles = active.loc[active.Seite == obstacle_side]
        if source.empty or obstacles.empty:
            continue
        for _, zone in source.iterrows():
            zl, zh = _float(zone.Untergrenze), _float(zone.Obergrenze)
            if zl is None or zh is None or zl >= zh:
                continue
            quality = _float(zone.get('Qualitaet_Punkte',0)) or 0.0
            if quality < quality_floor:
                continue
            if direction == 'Long':
                if zl > close + .15 * raw_atr:
                    continue
                planned_entry = zh + .05 * raw_atr
                stop = zl - .15 * raw_atr
                target_list = sorted((_float(r.Untergrenze) for r in obstacles.itertuples(index=False)
                                     if _float(r.Untergrenze) is not None), key=float)
                target_edges = [v for v in target_list if v > max(planned_entry, close) + .15 * raw_atr]
                if not target_edges:
                    continue
                target = target_edges[0] - .10 * raw_atr
                dist = max(0.0, close - zh) / raw_atr
            else:
                if zh < close - .15 * raw_atr:
                    continue
                planned_entry = zl - .05 * raw_atr
                stop = zh + .15 * raw_atr
                target_list = sorted((_float(r.Obergrenze) for r in obstacles.itertuples(index=False)
                                     if _float(r.Obergrenze) is not None), reverse=True)
                target_edges = [v for v in target_list if v < min(planned_entry, close) - .15 * raw_atr]
                if not target_edges:
                    continue
                target = target_edges[0] + .10 * raw_atr
                dist = max(0.0, zl - close) / raw_atr
            if dist > 3.0:
                continue  # far-off levels are not useful as near-term candidates
            # Closed 15m candle reaction + momentum confirmation; no guessing upcoming prices.
            confirmed = (_confirmed_reaction(frames['15Min'], direction=direction,
                            zone_low=zl, zone_high=zh, atr=raw_atr) and
                         tf15 == expected and tf5 != ('Abwärtstrend' if direction == 'Long' else 'Aufwärtstrend'))
            entry = close if confirmed else planned_entry
            geometry = (0 < stop < entry < target) if direction == 'Long' else (0 < target < entry < stop)
            if not geometry:
                continue
            rrr = abs(target-entry) / abs(entry-stop)
            base['evaluated'] += 1
            if rrr + 1e-10 < minimum_rrr:
                base['rejected_crv'] += 1
                continue
            context = assess_atr_target(direction=direction, entry=entry, stop=stop, target=target,
                atr=raw_atr, horizon_bars=horizon_bars, zones=active,
                higher_trend=trend_context['higher_trend'], middle_trend=trend_context['middle_trend'])
            # A 'confirmed' technical trigger must also clear meaningful ATR and obstacle warnings.
            state = ('TECHNISCHE REAKTION' if confirmed and context['status']=='KEIN UNMITTELBARER WARNHINWEIS'
                     else 'REAKTION MIT WARNHINWEIS' if confirmed else 'ZONE BEOBACHTEN')
            reasons = ('Geschlossene 15Min-Kerze berührt/reklamiert Zone; 15Min bestätigt Trend.' if confirmed
                       else 'Limit-Szenario an bestätigter Zone; noch keine Reaktionsbestätigung.')
            if context['status'] != 'KEIN UNMITTELBARER WARNHINWEIS':
                reasons += f" ATR-/Strukturprüfung: {context['status']}."
            ts = str(frames['15Min'].time.iloc[-1] + pd.Timedelta(minutes=15))
            results.append(Candidate(direction=direction, timeframe=timeframe, entry=entry, stop=stop,
                                     target=target, rrr=rrr, atr=raw_atr,
                                     stop_atr=abs(entry-stop)/raw_atr,
                                     target_atr=abs(target-entry)/raw_atr, quality=quality,
                                     zone_low=zl, zone_high=zh, price_distance_atr=dist,
                                     state=state, reason=reasons, target_context=context['status'],
                                     horizon_bars=horizon_bars, reference_close=close, signal_time_utc=ts))
    # Do NOT sort by highest RRR alone, which tends to favor distant/unreachable targets.
    rank = {'TECHNISCHE REAKTION':0,'REAKTION MIT WARNHINWEIS':1,'ZONE BEOBACHTEN':2}
    results.sort(key=lambda r:(rank[r.state], round(r.price_distance_atr,3), -r.quality,
                               -min(r.rrr,3.0), 0 if r.timeframe=='1H' else 1))
    base['candidates'] = results[:8]
    if not results:
        base['reason'] = 'Kein bestätigtes Zonenpaar mit ausreichendem Brutto-CRV gefunden.'
    return base


def projected_euro_result(*, entry: float, stop: float, target: float,
                          direction: str, nominal_eur: float) -> dict:
    """Illustrative gross returns for a *declared notional EUR exposure*, not margin.

    Linear price tracking is assumed; no broker-specific contract sizing,
    FX conversion, spreads, financing, gap risk or execution costs included.
    """
    if direction not in ('Long','Short') or not all(
        _float(x) is not None for x in (entry, stop, target, nominal_eur)
    ) or entry <= 0 or nominal_eur < 0:
        raise ValueError('Ungültige Planungseingaben')
    valid = (0 < stop < entry < target) if direction == 'Long' else (0 < target < entry < stop)
    if not valid:
        raise ValueError('Ungültige Preisstruktur')
    sign = 1 if direction == 'Long' else -1
    return {'gross_loss_eur': nominal_eur * abs(entry - stop) / entry,
            'gross_profit_eur': nominal_eur * sign * (target - entry) / entry,
            'notional_eur': nominal_eur}
