"""Regionale Session-Referenzfenster auf Basis abgeschlossener OHLC-Kerzen.

KEIN offizieller Börsenkalender und KEINE Aussage über Orderbuch-Liquidität.
Die Fenster sind absichtlich disjunkt, damit jede Kerze höchstens einem
farbigen Hintergrund zugeordnet wird:
  Asien: 09:00 Tokyo -> 08:00 London
  Europa: 08:00 London -> 08:00 New York
  USA: 08:00 -> 16:00 New York
Zwischen US-Ende und Beginn Asiens wird kein Kasten gezeichnet.
IANA-Zeitzonen berücksichtigen die jeweiligen Sommerzeit-Regeln.

Die H/L einer beendeten Sitzung werden ausschließlich veröffentlicht, wenn
alle erwarteten (vorher abgeschlossenen) Kerzen des Zeitfensters vorliegen.
Eine laufende Session kann mit einem vorläufigen Kasten gezeigt werden, erhält
aber KEINE vermeintlich finalen Hoch-/Tiefmarken.
"""
from dataclasses import dataclass
import math
from typing import List
import pandas as pd
import plotly.graph_objects as go

SESSION_COLORS = {
    'Asien': {'line': '#9A8CFF', 'shade': 'rgba(154,140,255,0.13)', 'short': 'AS'},
    'Europa': {'line': '#44D1A5', 'shade': 'rgba(68,209,165,0.12)', 'short': 'EU'},
    'USA': {'line': '#FFA967', 'shade': 'rgba(255,169,103,0.12)', 'short': 'US'},
}
SUPPORTED_MINUTES = (5, 15, 60)

@dataclass(frozen=True)
class SessionRange:
    name: str
    start: pd.Timestamp  # tz-aware UTC
    end: pd.Timestamp  # tz-aware UTC
    high: float
    low: float
    complete: bool
    bars: int
    expected_bars: int


def _aware_utc(ts):
    stamp = pd.Timestamp(ts)
    return stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')


def _region_time(day, hour, zone):
    """Local wall-clock start converted to UTC (DST-safe for these daytime hours)."""
    return pd.Timestamp(day).tz_localize(zone).replace(hour=hour, minute=0, second=0, microsecond=0).tz_convert('UTC')


def session_windows(day):
    """Disjoint reference intervals for a single UTC calendar date."""
    base = pd.Timestamp(day).date()
    asia = _region_time(base, 9, 'Asia/Tokyo')
    london = _region_time(base, 8, 'Europe/London')
    us_start = _region_time(base, 8, 'America/New_York')
    us_end = _region_time(base, 16, 'America/New_York')
    if not (asia < london < us_start < us_end):
        raise ValueError('Zeitfenster überschneiden sich oder sind nicht geordnet.')
    return [('Asien', asia, london), ('Europa', london, us_start), ('USA', us_start, us_end)]


def collect_sessions(candles, minutes, asof=None):
    """Identify fully-covered completed sessions plus an optional live window.

    OHLC timestamps are *bar opens* in UTC. No incomplete/ongoing candle should
    be supplied by the caller. Historical windows missing even one bar are
    omitted rather than being falsely reported as complete session highs/lows.
    """
    if int(minutes) not in SUPPORTED_MINUTES:
        return []
    if candles is None or candles.empty:
        return []
    d = candles[['time', 'high', 'low']].copy()
    d['time'] = pd.to_datetime(d['time'], utc=True)
    d = d.dropna(subset=['time', 'high', 'low']).sort_values('time')
    if d.time.duplicated().any():
        raise ValueError('Doppelte OHLC-Zeitstempel: Sessions können nicht eindeutig ausgewertet werden.')
    if d.empty:
        return []
    delta = pd.Timedelta(minutes=int(minutes))
    newest_close = d.time.iloc[-1] + delta
    effective_now = _aware_utc(asof) if asof is not None else pd.Timestamp.now(tz='UTC')
    start_day = (d.time.iloc[0] - pd.Timedelta(days=1)).date()
    end_day = (d.time.iloc[-1] + pd.Timedelta(days=1)).date()
    results = []
    for day in pd.date_range(start_day, end_day, freq='D'):
        for name, start, end in session_windows(day):
            if end <= d.time.iloc[0] or start > d.time.iloc[-1]:
                continue
            segment = d[(d.time >= start) & (d.time < end)]
            if segment.empty:
                continue
            expected = int((end - start) // delta)
            if (end - start) != expected * delta or expected < 1:
                continue
            candle_times = pd.DatetimeIndex(segment.time)
            complete_data = len(segment) == expected and candle_times.equals(
                pd.date_range(start, periods=expected, freq=delta))
            session_ended = end <= min(effective_now, newest_close)
            if session_ended and complete_data:
                results.append(SessionRange(name, start, end, float(segment.high.max()),
                                            float(segment.low.min()), True, len(segment), expected))
            elif (start <= effective_now < end and not session_ended and
                  # Only show a genuinely current in-progress window when data are fresh
                  newest_close <= effective_now and effective_now - newest_close <= 2 * delta and
                  segment.time.iloc[0] == start and
                  candle_times.equals(pd.date_range(start, periods=len(segment), freq=delta))):
                results.append(SessionRange(name, start, end, float(segment.high.max()),
                                            float(segment.low.min()), False, len(segment), expected))
    return results


def _price_text(value):
    if not math.isfinite(value):
        return '—'
    digits = 2 if abs(value) >= 1000 else 3 if abs(value) >= 10 else 5
    return f'{value:.{digits}f}'


def add_session_overlay(fig, sessions: List[SessionRange], first_visible, last_visible,
                        names=None, show_high_low=True, show_names=True):
    """Session backgrounds behind price candles, H/L only on finished sessions."""
    names = set(SESSION_COLORS) if names is None else set(names)
    first_visible = _aware_utc(first_visible)
    last_visible = _aware_utc(last_visible)
    for session in sessions:
        if session.name not in names or session.end <= first_visible or session.start >= last_visible:
            continue
        style = SESSION_COLORS[session.name]
        left, right = max(session.start, first_visible), min(session.end, last_visible)
        if right <= left:
            continue
        # Plotly rectangular shape is clipped to the main candle subplot.
        fig.add_shape(type='rect', x0=left, x1=right, y0=session.low, y1=session.high,
                      line=dict(color=style['line'], width=1, dash='dot'),
                      fillcolor=style['shade'], layer='below', row=1, col=1)
        if show_names:
            mid = left + (right - left)/2
            title = session.name if session.complete else session.name + ' · läuft'
            fig.add_annotation(x=mid, xref='x', y=session.high, yref='y', yshift=5,
                               showarrow=False, text=f'<b>{title}</b>',
                               xanchor='center', yanchor='bottom',
                               font=dict(size=10, color=style['line']),
                               bgcolor='rgba(11,17,32,0.80)', borderpad=2)
        if show_high_low and session.complete:
            # Lines span the session, NOT the whole chart, to avoid dozens of lines.
            for prefix, price in [('H', session.high), ('L', session.low)]:
                fig.add_shape(type='line', x0=left, x1=right, y0=price, y1=price,
                              line=dict(color=style['line'], width=1.3, dash='dash'),
                              layer='below', row=1, col=1)
                # One compact label at the right end (more details in the table).
                fig.add_annotation(x=right, xref='x', y=price, yref='y',
                                   text=f"{style['short']} {prefix} {_price_text(price)}",
                                   showarrow=False, xanchor='right', yanchor='middle',
                                   font=dict(size=9, color=style['line']),
                                   bgcolor='rgba(11,17,32,0.85)', borderpad=1)
    return fig


def completed_session_table(sessions, selected_names, time_labeler, max_rows=15):
    rows=[]
    for session in sorted(sessions, key=lambda x: x.start, reverse=True):
        if not session.complete or session.name not in selected_names:
            continue
        rows.append({'Session':session.name,
                     'Beginn': time_labeler(session.start),
                     'Ende': time_labeler(session.end),
                     'High':session.high, 'Low':session.low})
        if len(rows)>=max_rows:
            break
    return pd.DataFrame(rows, columns=['Session','Beginn','Ende','High','Low'])
