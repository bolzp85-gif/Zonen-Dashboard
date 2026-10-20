"""Preisbasierte Multi-Timeframe-Trenderkennung für abgeschlossene OHLC-Kerzen.

Keine Orderbuchdaten, keine Wahrscheinlichkeitswerte, keine Trades.
Die Klassifikation setzt bestätigte lokale Hochs/Tiefs und EMA-Filter voraus.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

import numpy as np
import pandas as pd

from engine import atr, TF

FRAMES = ('1D', '4H', '1H', '15Min', '5Min')


@dataclass(frozen=True)
class Pivot:
    index: int
    known: int
    price: float


def _pivots(d: pd.DataFrame, side: str, window: int = 3) -> list[Pivot]:
    """Keine Lookahead-Pivots: Extrempunkt p wird erst bei p+window bekannt.

    Differenzen aus nur einem Docht werden durch eine ATR-Mindestreaktion
    zurückhaltend interpretiert. Gleich hohe Extrempunkte sind keine eindeutigen
    einzigartigen Pivots in demselben lokalen Fenster.
    """
    assert side in ('high', 'low')
    values = d[side].to_numpy(dtype=float)
    tr = atr(d, length=14).to_numpy(dtype=float)
    out = []
    for p in range(window, len(d) - window):
        if not (np.isfinite(values[p]) and np.isfinite(tr[p]) and tr[p] > 0):
            continue
        segment = values[p-window:p+window+1]
        extreme = np.max(segment) if side == 'high' else np.min(segment)
        if values[p] != extreme or np.sum(segment == extreme) != 1:
            continue
        # Schlüssig sichtbare Gegenbewegung in den Folgekerzen, die im
        # Zeitpunkt p+window bereits feststeht. Keine Zukunftskenntnis.
        following = d.low.iloc[p+1:p+window+1].min() if side == 'high' else d.high.iloc[p+1:p+window+1].max()
        reaction = (values[p] - following) if side == 'high' else (following - values[p])
        if not np.isfinite(reaction) or reaction < 0.30 * tr[p]:
            continue
        # Nicht jedes winzige Rauschen als zusätzlichen Pivot zählen.
        if out and p - out[-1].index < window+1:
            previous = out[-1]
            if (values[p] > previous.price if side == 'high' else values[p] < previous.price):
                out[-1] = Pivot(p, p + window, float(values[p]))
            continue
        out.append(Pivot(p, p + window, float(values[p])))
    return out


def _swing_direction(highs: list[Pivot], lows: list[Pivot], scale: float) -> str:
    if len(highs) < 2 or len(lows) < 2:
        return 'Nicht bestätigt'
    h_move = highs[-1].price - highs[-2].price
    l_move = lows[-1].price - lows[-2].price
    threshold = 0.15 * scale
    if h_move > threshold and l_move > threshold:
        return 'Höhere Hochs und Tiefs'
    if h_move < -threshold and l_move < -threshold:
        return 'Tiefere Hochs und Tiefs'
    if abs(h_move) <= threshold and abs(l_move) <= threshold:
        return 'Ähnliche Hochs und Tiefs'
    return 'Uneinheitlich'


def analyze_trend(d: pd.DataFrame, timeframe: str, pivot_window: int = 3) -> dict:
    """Klassifiziere den *aktuellen* Datenstand ohne unbestätigte Pivots."""
    if timeframe not in FRAMES:
        raise ValueError('Unbekannte Zeitebene')
    if pivot_window < 2:
        raise ValueError('Pivot-Fenster mindestens 2')
    if len(d) < 40:
        raise ValueError('Trendradar benötigt mindestens 40 abgeschlossene Kerzen')
    for key in ('time', 'open', 'high', 'low', 'close'):
        if key not in d.columns:
            raise ValueError(f'Fehlende Spalte: {key}')
    if not d.time.is_monotonic_increasing or d.time.duplicated().any():
        raise ValueError('Kerzenzeiten sind nicht eindeutig aufsteigend')
    if (d.high < d.low).any():
        raise ValueError('Ungültige OHLC-Kerze')

    high_pivots = _pivots(d, 'high', pivot_window)
    low_pivots = _pivots(d, 'low', pivot_window)
    a = float(atr(d, length=14).iloc[-1])
    close = float(d.close.iloc[-1])
    if not math.isfinite(a) or a <= 0:
        raise ValueError('ATR ist nicht verfügbar')

    ema20 = d.close.ewm(span=20, adjust=False, min_periods=20).mean()
    ema50 = d.close.ewm(span=50, adjust=False, min_periods=50).mean()
    ema200 = d.close.ewm(span=200, adjust=False, min_periods=200).mean()
    slope = float((ema50.iloc[-1] - ema50.iloc[-6]) / a) if len(d) >= 55 else float('nan')
    trend_ema = 'Nicht verfügbar'
    if np.isfinite(slope) and np.isfinite(ema20.iloc[-1]) and np.isfinite(ema50.iloc[-1]):
        if ema20.iloc[-1] > ema50.iloc[-1] and close > ema50.iloc[-1] and slope >= .10:
            trend_ema = 'Aufwärts'
        elif ema20.iloc[-1] < ema50.iloc[-1] and close < ema50.iloc[-1] and slope <= -.10:
            trend_ema = 'Abwärts'
        elif abs(slope) < .12:
            trend_ema = 'Flach'
        else:
            trend_ema = 'Uneinheitlich'

    structure = _swing_direction(high_pivots, low_pivots, a)
    # Struktur kann veraltet sein (trendstarker Markt ohne jüngere lokale Pivots).
    latest_pivot_index = max((p.index for p in high_pivots[-1:] + low_pivots[-1:]), default=-1)
    if latest_pivot_index < len(d) - 1 - 65:
        structure = 'Nicht bestätigt (alte Pivots)'

    warning = 'Kein bestätigter Gegentrendbruch'
    if len(d) >= 2 and len(low_pivots) >= 2 and structure == 'Höhere Hochs und Tiefs':
        low = low_pivots[-1]
        if low.known <= len(d)-2 and (d.close.tail(2) < low.price - .10*a).all():
            warning = 'Zwei Schlusskurse unter letztem bestätigten höheren Tief'
    if len(d) >= 2 and len(high_pivots) >= 2 and structure == 'Tiefere Hochs und Tiefs':
        high = high_pivots[-1]
        if high.known <= len(d)-2 and (d.close.tail(2) > high.price + .10*a).all():
            warning = 'Zwei Schlusskurse über letztem bestätigten tieferen Hoch'

    if structure == 'Höhere Hochs und Tiefs' and trend_ema == 'Aufwärts' and warning.startswith('Kein'):
        trend = 'Aufwärtstrend'
    elif structure == 'Tiefere Hochs und Tiefs' and trend_ema == 'Abwärts' and warning.startswith('Kein'):
        trend = 'Abwärtstrend'
    elif structure == 'Ähnliche Hochs und Tiefs' and trend_ema in ('Flach', 'Uneinheitlich'):
        trend = 'Seitwärts'
    elif structure == 'Uneinheitlich' and trend_ema == 'Flach' and np.isfinite(ema50.iloc[-1]) and abs(close - float(ema50.iloc[-1])) <= 1.1*a:
        trend = 'Seitwärts'
    else:
        trend = 'Übergang / unklar'

    ema200_label = 'Keine EMA 200 verfügbar' if not np.isfinite(ema200.iloc[-1]) else (
        'Über EMA 200' if close > ema200.iloc[-1] else 'Unter EMA 200')
    if len(d) < 600:
        ema200_label += ' · begrenzter EMA-Vorlauf' if len(d) >= 200 else ''

    return {
        'Zeitebene': timeframe,
        'Trend': trend,
        'Marktstruktur': structure,
        'EMA_Filter': trend_ema,
        'EMA50_Steigung_ATR_5': round(slope, 3) if math.isfinite(slope) else np.nan,
        'EMA200_Kontext': ema200_label,
        'Strukturwarnung': warning,
        'Swing_Hoch': high_pivots[-1].price if high_pivots else np.nan,
        'Swing_Tief': low_pivots[-1].price if low_pivots else np.nan,
        'Bestaetigte_Hochs': len(high_pivots),
        'Bestaetigte_Tiefs': len(low_pivots),
        'ATR_14': a,
        'Schlusskurs': close,
        'Letzter_Schluss_UTC': pd.Timestamp(d.time.iloc[-1]) + pd.Timedelta(minutes=TF[timeframe][1]),
        'Historienkerzen': len(d),
        'Pivot_Fenster': pivot_window,
    }


def analyze_frames(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    missing = [f for f in FRAMES if f not in frames]
    if missing:
        raise ValueError('Fehlende Zeitebenen: ' + ', '.join(missing))
    return pd.DataFrame([analyze_trend(frames[tf], tf) for tf in FRAMES])


def interpret_multitimeframe(rows: pd.DataFrame) -> str:
    """Sachliche Einordnung; keine Kurs- oder Handelsprognose."""
    state = dict(zip(rows.Zeitebene, rows.Trend))
    h4, h1, m15, m5 = (state[k] for k in ('4H', '1H', '15Min', '5Min'))
    if h4 == h1 == 'Aufwärtstrend':
        if m15 == 'Abwärtstrend':
            return '4H und 1H aufwärts; 15 Min abwärts: kurzfristige Korrektur innerhalb eines übergeordneten Aufwärtstrends möglich. 5 Min: ' + m5 + '.'
        return '4H und 1H bestätigen einen Aufwärtstrend. 15 Min: ' + m15 + '; 5 Min: ' + m5 + '. Keine automatische Einstiegsfreigabe.'
    if h4 == h1 == 'Abwärtstrend':
        if m15 == 'Aufwärtstrend':
            return '4H und 1H abwärts; 15 Min aufwärts: kurzfristige Gegenbewegung innerhalb des übergeordneten Abwärtstrends möglich. 5 Min: ' + m5 + '.'
        return '4H und 1H bestätigen einen Abwärtstrend. 15 Min: ' + m15 + '; 5 Min: ' + m5 + '. Keine automatische Einstiegsfreigabe.'
    if h4 in ('Aufwärtstrend', 'Abwärtstrend') and h1 in ('Aufwärtstrend', 'Abwärtstrend') and h4 != h1:
        return '4H und 1H widersprechen einander. Korrektur oder Übergang – übergeordnet keine einheitliche Richtung.'
    if h4 == 'Seitwärts' or h1 == 'Seitwärts':
        return 'Mindestens eine übergeordnete Zeitebene befindet sich seitwärts; Ausbrüche und Range-Grenzen getrennt beurteilen.'
    return 'Übergeordnete Richtung nicht ausreichend bestätigt. Strukturelle Bestätigung abwarten; kein erzwungenes Trendsignal.'


def zone_alignment(zones: pd.DataFrame, frame_trends: pd.DataFrame, max_distance_atr: float = .75) -> pd.DataFrame:
    """Preislicher Kontext der *bereits vorhandenen* aktiven S/R-Zonen je TF.

    Verwendet keine neuen Zonen und überschreibt keine Zonenqualitäten.
    """
    rows = []
    for r in frame_trends.itertuples(index=False):
        tf = r.Zeitebene
        active = zones.loc[(zones['Zeitebene'] == tf) & (zones['Status'] != 'Gebrochen')]
        price, a = float(r.Schlusskurs), float(r.ATR_14)
        nearest = {}
        for side in ('Unterstützung', 'Widerstand'):
            part = active[active['Typ'] == side]
            if part.empty:
                nearest[side] = (np.nan, np.nan)
                continue
            # 0 Distanz bei Berührung, andernfalls Abstand zum Rand.
            distances = (part['Untergrenze'] - price).clip(lower=0) + (price - part['Obergrenze']).clip(lower=0)
            idx = distances.idxmin()
            z = part.loc[idx]
            nearest[side] = ((float(z.Untergrenze) + float(z.Obergrenze)) / 2,
                             float(distances.loc[idx]) / a)
        matching = ('Unterstützung' if r.Trend == 'Aufwärtstrend' else
                    'Widerstand' if r.Trend == 'Abwärtstrend' else None)
        note = ('Keine eindeutige Trendrichtung' if matching is None else
                f'{matching} im Nähebereich' if nearest[matching][1] <= max_distance_atr else
                'Keine trendseitige S/R-Zone nahe am Kurs')
        rows.append({'Zeitebene': tf, 'Trend': r.Trend,
                     'Nächste_Unterstützung': nearest['Unterstützung'][0],
                     'Unterstützung_Distanz_ATR': nearest['Unterstützung'][1],
                     'Nächster_Widerstand': nearest['Widerstand'][0],
                     'Widerstand_Distanz_ATR': nearest['Widerstand'][1],
                     'S_R_Trendabgleich': note})
    return pd.DataFrame(rows)
