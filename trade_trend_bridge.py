"""Read-only bridge: exact Zonenradar trend states -> Trade-Pilot.

No second trend algorithm, no silent long/short conversion of unclear signals.
Uses confirmed trend_radar.analyze_frames() output without recalculating pivots.
"""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd

PRIMARY_FRAMES = ('4H', '1H', '15Min')
ALL_FRAMES = ('1D', '4H', '1H', '15Min', '5Min')
KNOWN = {'Aufwärtstrend', 'Abwärtstrend', 'Seitwärts', 'Übergang / unklar'}


@dataclass(frozen=True)
class FrameContext:
    timeframe: str
    trend: str
    structure: str
    ema_filter: str
    candle_close_utc: str
    available: bool


def derive_trend_context(trends: pd.DataFrame | None, *, feed_stale: bool = False) -> dict:
    """Trust only exact frame rows with complete expected fields.

    An inconsistent/missing row is not assumed neutral or up/down.
    Feed staleness is carried along independently of the calculated trend.
    """
    rows: dict[str, FrameContext] = {}
    if isinstance(trends, pd.DataFrame) and not trends.empty and 'Zeitebene' in trends:
        for tf in ALL_FRAMES:
            match = trends.loc[trends['Zeitebene'] == tf]
            if len(match) != 1:
                continue
            row = match.iloc[0]
            trend = str(row.get('Trend', ''))
            structure = str(row.get('Marktstruktur', ''))
            ema_filter = str(row.get('EMA_Filter', ''))
            if trend not in KNOWN or not structure or structure in ('nan','None') or not ema_filter or ema_filter in ('nan','None'):
                continue
            candle = row.get('Letzter_Schluss_UTC')
            stamp = str(candle) if pd.notna(candle) else 'k. A.'
            rows[tf] = FrameContext(tf, trend, structure, ema_filter, stamp, True)
    for tf in ALL_FRAMES:
        rows.setdefault(tf, FrameContext(tf, 'Nicht verfügbar', 'Nicht verfügbar', 'Nicht verfügbar', 'k. A.', False))

    h4, h1 = rows['4H'].trend, rows['1H'].trend
    if h4 == h1 == 'Aufwärtstrend':
        bias = 'Long'
    elif h4 == h1 == 'Abwärtstrend':
        bias = 'Short'
    else:
        bias = 'Neutral / uneinheitlich'
    reliable = all(rows[tf].available for tf in PRIMARY_FRAMES) and not feed_stale
    return {'frames': rows, 'higher_trend': h4, 'middle_trend': h1,
            'entry_trend': rows['15Min'].trend, 'bias': bias,
            'complete': all(rows[tf].available for tf in PRIMARY_FRAMES),
            'current': reliable, 'feed_stale': bool(feed_stale)}


def direction_conflict(context: dict, direction: str) -> bool:
    """A known opposed 4H trend blocks the proposed trade direction."""
    return ((direction == 'Long' and context['higher_trend'] == 'Abwärtstrend') or
            (direction == 'Short' and context['higher_trend'] == 'Aufwärtstrend'))
