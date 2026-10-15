"""Transparent, purely OHLC-based and retrospective zone quality annotations.

No order book, bid/ask sizes, true volume, probability or statistical evidence.
Known/current states are never used to backdate a trade signal.
"""
import numpy as np
import pandas as pd


QUALITY_COLUMNS = ('Qualitaet_Punkte', 'Qualitaet_Klasse', 'Abweisungen_geprueft',
                   'Abweisungen_ab_0_5_ATR', 'Mittlere_Abweisung_ATR',
                   'Entstehungs_ATR', 'Alter_Kerzen', 'Frische')


def assess_zone(d, zone, reaction_bars=3, reaction_atr=0.5, evaluation=None):
    """Measure first-touch reactions using CLOSES through available closed bars.

    For any touch, the largest favourable close displacement over the next
    reaction_bars bars (including touch bar), measured relative to the nearest
    supportive zone edge, is recorded. The future is not used by zone discovery;
    this is a current retrospective annotation only.
    """
    if not 1 <= reaction_bars <= 20:
        raise ValueError('Reaktionsfenster muss zwischen 1 und 20 Kerzen liegen.')
    if not np.isfinite(zone.scale) or zone.scale <= 0:
        raise ValueError('Die Entstehungs-ATR muss positiv sein.')
    from engine import evaluate
    evaluation = evaluate(d, zone) if evaluation is None else evaluation
    status = evaluation['Status']
    first = zone.known + 1
    tested = False
    break_count = 0
    observed = []
    n = len(d)
    lows = d.low.to_numpy(dtype=float)
    highs = d.high.to_numpy(dtype=float)
    all_closes = d.close.to_numpy(dtype=float)
    for i in range(first, n):
        touch = bool(lows[i] <= zone.high and highs[i] >= zone.low)
        bad_close = all_closes[i] < zone.low-0.1*zone.scale if zone.side == 'support' else all_closes[i] > zone.high+0.1*zone.scale
        break_count = break_count+1 if bad_close else 0
        if break_count >= 2:
            break
        if touch and not tested and i+reaction_bars <= n:
            # A closing price beyond the other side is not by itself proof of
            # liquidity absorption; it is simply favourable price movement.
            closes = all_closes[i:i+reaction_bars]
            excursion = (float(np.max(closes))-zone.high) / zone.scale if zone.side == 'support' else (zone.low-float(np.min(closes))) / zone.scale
            observed.append(max(0.0, excursion))
        tested = touch

    age = n-1-zone.known
    tests = evaluation['Tests']
    # Wholly descriptive engineering score; not a success probability.
    # Higher impulse from the founding pattern, fewer revisits, fewer old bars,
    # and reactions actually measured using fully closed windows.
    impulse = min(max(zone.strength, 0.0), 3.0)
    base_points = 52 + 8*impulse
    # Confirmed S/R has two independent founding pivots, or a breakout retest.
    # Do not award 'untested' bonuses or automatically penalize additional
    # tests: repeated successful rejections are information, not a defect.
    base_points -= min(14, age/30)
    mean_excursion = float(np.mean(observed)) if observed else np.nan
    positive = sum(value >= reaction_atr for value in observed)
    if observed:
        # Reward completed favourable reaction windows only. Multiple contacts
        # with no meaningful reaction do not increase the score.
        reaction_ratio = positive / len(observed)
        base_points += 12*reaction_ratio + 8*min(mean_excursion/1.5, 1.0)
        if len(observed) >= 2:
            base_points += min(8, 2*(len(observed)-1)) * reaction_ratio
    if status == 'Gebrochen':
        base_points = min(base_points, 20)
    points = int(np.clip(round(base_points), 0, 100))
    return {
        'Qualitaet_Punkte': points,
        'Qualitaet_Klasse': 'hoch' if points >= 70 else 'mittel' if points >= 50 else 'niedrig',
        'Abweisungen_geprueft': len(observed),
        'Abweisungen_ab_0_5_ATR': int(positive),
        'Mittlere_Abweisung_ATR': round(mean_excursion, 3) if observed else np.nan,
        'Entstehungs_ATR': float(zone.scale),
        'Alter_Kerzen': age,
        'Frische': 'Keine weiteren Besuche' if tests == 0 else f'{tests} weitere(r) Besuch(e)',
    }


def assess_zones(d, zones, evaluations=None):
    if evaluations is None:
        evaluations = [None]*len(zones)
    if len(evaluations) != len(zones):
        raise ValueError("Zonen und Zonenbewertungen müssen die gleiche Länge haben.")
    return pd.DataFrame([assess_zone(d, zone, evaluation=ev) for zone, ev in zip(zones, evaluations)], columns=QUALITY_COLUMNS)
