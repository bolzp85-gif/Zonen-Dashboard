"""OHLC-only 15m market-structure reversal watcher comparing 61.8% and 78.6%.

No orders, probabilities, orderbook interpretation or fees. Uses only closed bars.
Signals use pre-known (three-right-bar confirmed) pivots. A 15m structure break
can be detected against a 1H trend, but such a countertrend is NEVER called an
independently confirmed higher-timeframe reversal.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from typing import Any
import pandas as pd
from engine import atr
from trend_radar import _pivots
from atr_target_context import assess as assess_target


@dataclass(frozen=True)
class ReversalIdea:
    direction: str
    kind: str
    state: str
    break_level: float
    broken_at_utc: str
    impulse_start: float
    impulse_end: float
    fib_786: float
    fib_618: float
    fib_ratio: float
    zone_tested: bool
    entry_reference: float
    stop_reference: float
    target_reference: float
    gross_rrr: float
    atr15: float
    stop_atr: float
    target_atr: float
    sr_confluence: str
    fvg_context: str
    higher_tf_context: str
    confirmation: str
    warning: str
    target_context: str


def _price(x: Any) -> float | None:
    try:
        value = float(x)
        return value if isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _data_ok(d: pd.DataFrame, n: int) -> bool:
    if not isinstance(d, pd.DataFrame) or len(d) < n or not {'time','open','high','low','close'}.issubset(d.columns):
        return False
    try:
        numeric = d[['open','high','low','close']].astype(float)
        return (numeric.notna().all().all() and numeric.map(isfinite).all().all() and
                not d.time.duplicated().any() and d.time.is_monotonic_increasing and
                (numeric.high >= numeric[['open','close','low']].max(axis=1)).all() and
                (numeric.low <= numeric[['open','close','high']].min(axis=1)).all())
    except (ValueError, TypeError, AttributeError):
        return False


def _prior_structure(highs: list, lows: list, *, before: int, a: float) -> str:
    h = [p for p in highs if p.known < before]
    l = [p for p in lows if p.known < before]
    if len(h) < 2 or len(l) < 2:
        return 'Unklar'
    dh, dl = h[-1].price-h[-2].price, l[-1].price-l[-2].price
    if dh > .12*a and dl > .12*a:
        return 'Aufwärts'
    if dh < -.12*a and dl < -.12*a:
        return 'Abwärts'
    return 'Unklar'


def _last_break(d: pd.DataFrame, direction: str, highs: list, lows: list,
                atrs: pd.Series, window: int):
    """Detect the latest crossing past a confirmed swing from BEFORE the break.

    This intentionally also detects BOS continuation, but tags it differently
    from CHoCH if the prior 15m swing structure was not opposite.
    """
    events = []
    for i in range(max(40, len(d)-window), len(d)):
        a = _price(atrs.iloc[i])
        if a is None or a <= 0:
            continue
        previous = [p for p in (lows if direction=='Short' else highs) if p.known < i]
        if not previous:
            continue
        pivot = previous[-1]
        prior = _prior_structure(highs, lows, before=i, a=a)
        if direction == 'Short':
            crossed = (d.close.iloc[i-1] >= pivot.price-.10*a and
                       d.close.iloc[i] < pivot.price-.10*a)
        else:
            crossed = (d.close.iloc[i-1] <= pivot.price+.10*a and
                       d.close.iloc[i] > pivot.price+.10*a)
        # A weekend/session gap is not a continuous candle-close structure break.
        gap = pd.Timestamp(d.time.iloc[i])-pd.Timestamp(d.time.iloc[i-1])
        if not crossed or gap != pd.Timedelta(minutes=15):
            continue
        # Countertrend change of character only when a preceding swing structure exists.
        kind = ('CHoCH' if (direction=='Short' and prior=='Aufwärts') or
                (direction=='Long' and prior=='Abwärts') else
                'BOS' if (direction=='Short' and prior=='Abwärts') or
                            (direction=='Long' and prior=='Aufwärts') else 'STRUKTURBRUCH')
        other = [p for p in (highs if direction=='Short' else lows) if p.known < i]
        if not other:
            continue
        anchor = other[-1]
        # Swings from too far back should not become arbitrary Fibonacci anchors.
        if i-anchor.index > 52 or anchor.index >= i:
            continue
        events.append((i, float(pivot.price), float(anchor.price), kind))
    return events[-1] if events else None


def _zone_match(zones: pd.DataFrame | None, fib: float, atr15: float, direction: str, ratio: float=.786) -> tuple[str, bool]:
    if zones is None or not isinstance(zones, pd.DataFrame):
        return 'Keine bestätigte S/R-Zone in der Nähe nachgewiesen', False
    columns = {'Seite','Status','Untergrenze','Obergrenze'}
    if not columns.issubset(zones.columns):
        return 'S/R-Datenformat unvollständig', False
    side = 'Widerstand' if direction=='Short' else 'Unterstützung'
    for _, z in zones.iterrows():
        if z['Seite'] != side or z['Status'] == 'Gebrochen':
            continue
        low, high = _price(z['Untergrenze']), _price(z['Obergrenze'])
        if low is None or high is None or low > high:
            continue
        dist = max(low-fib, fib-high, 0.)
        if dist <= .30*atr15:
            tf = str(z.get('Zeitebene','?'))
            return f'Bestätigte {side.lower()} ({tf}), {low:.5f}–{high:.5f}', True
    return f'Kein bestätigter S/R-Bereich bei {ratio*100:.1f} %', False


def _fvg_near(d: pd.DataFrame, direction: str, fib: float, a: float,
              start: int) -> str:
    """Pure OHLC three-candle non-overlap, NOT actual order-book imbalance."""
    for i in range(len(d)-1, max(start+1, len(d)-52), -1):
        if i < 2:
            break
        left, right = d.iloc[i-2], d.iloc[i]
        if direction == 'Short' and right.high < left.low:
            low, high = float(right.high), float(left.low)
        elif direction == 'Long' and right.low > left.high:
            low, high = float(left.high), float(right.low)
        else:
            continue
        if high-low >= .15*a and max(low-fib, fib-high, 0) <= .30*a:
            return f'Historisches Drei-Kerzen-FVG {low:.5f}–{high:.5f} (nur OHLC; Füllstatus nicht geprüft)'
    return 'Kein passendes Drei-Kerzen-FVG erkannt'


def _five_min_rejection(d: pd.DataFrame, direction: str, fib: float, atr15: float,
                        after_utc) -> bool:
    if len(d) < 20:
        return False
    a5 = _price(atr(d).iloc[-1])
    if a5 is None or a5 <= 0:
        return False
    # Only final TWO completed five-minute candles can trigger a fresh alert.
    for i in range(max(1,len(d)-2),len(d)):
        b = d.iloc[i]
        b_end = pd.Timestamp(b.time)+pd.Timedelta(minutes=5)
        if b_end.tzinfo is None:
            b_end = b_end.tz_localize('UTC')
        if b_end <= after_utc or pd.Timestamp(d.time.iloc[i])-pd.Timestamp(d.time.iloc[i-1]) != pd.Timedelta(minutes=5):
            continue
        if direction=='Short' and (b.high >= fib-.20*atr15 and
                    b.close < fib-.10*a5 and b.close < b.open and
                    b.close < d.close.iloc[i-1]):
            return True
        if direction=='Long' and (b.low <= fib+.20*atr15 and
                    b.close > fib+.10*a5 and b.close > b.open and
                    b.close > d.close.iloc[i-1]):
            return True
    return False


def scan_reversals(*, frames: dict, zones: pd.DataFrame | None,
                   trend_context: dict, minimum_rrr: float=1.8,
                   history_bars: int=48, fib_tolerance_atr: float=.25,
                   horizon_bars: int=8, is_demo: bool=False, stale: bool=False,
                   levels: tuple[float, ...]=(.618, .786)) -> dict:
    """Candidate event chains only; never a trading approval or profit prediction.

    A CHoCH is a warning, not proof of a 1H/4H trend reversal. Fibonacci
    ratios compare exactly the SAME closed, confirmed 15m impulse. Neither
    ratio is assumed superior; results are planning scenarios, not outcomes.
    """
    if (not .5 <= minimum_rrr <= 10 or not 12 <= history_bars <= 120 or
        not .05 <= fib_tolerance_atr <= .75 or not 1 <= horizon_bars <= 120 or
        not levels or any(r not in (.618, .786) for r in levels) or len(set(levels)) != len(levels)):
        raise ValueError('Ungültige Scanner-Parameter')
    res = {'ideas':[], 'early_breaks':[], 'reason':'', 'checked':0}
    if is_demo or stale or not trend_context.get('complete') or not trend_context.get('current'):
        res['reason'] = 'Demo, veraltete oder unvollständige Daten: keine aktuellen Setups.'
        return res
    if not all(tf in frames and _data_ok(frames[tf], 65 if tf=='15Min' else 20)
               for tf in ('15Min','5Min')):
        res['reason'] = '15M oder 5M: zu wenige oder ungültige abgeschlossene OHLC-Kerzen.'
        return res
    d, d5 = frames['15Min'], frames['5Min']
    hi, lo = _pivots(d, 'high', window=3), _pivots(d,'low',window=3)
    at = atr(d)
    a = _price(at.iloc[-1])
    if not a or a <= 0:
        res['reason'] = 'ATR 15M fehlt.'
        return res
    h4, h1 = trend_context['higher_trend'],trend_context['middle_trend']
    for direction in ('Short','Long'):
        event = _last_break(d, direction, hi, lo, at, history_bars)
        if event is None:
            continue
        i, break_level, anchor, kind = event
        # Need a new, confirmed opposite pivot AFTER the break.
        end_pivots = [p for p in (lo if direction=='Short' else hi)
                      if p.index >= i and p.known <= len(d)-1]
        if not end_pivots:
            res['early_breaks'].append({'direction': direction, 'kind': kind,
                'level': break_level,
                'time_utc': str(pd.Timestamp(d.time.iloc[i]) + pd.Timedelta(minutes=15)),
                'next': 'Impulsende auf 15M noch nicht durch drei Folgekerzen bestätigt'})
            continue
        endpoint = (min(end_pivots, key=lambda p:p.price) if direction=='Short'
                    else max(end_pivots, key=lambda p:p.price))
        # From the first post-break opposite swing; no future or arbitrary anchor.
        end = float(endpoint.price)
        if (anchor <= end if direction=='Short' else anchor >= end):
            continue
        endpoint_atr = _price(at.iloc[endpoint.known])
        if endpoint_atr is None or endpoint_atr <= 0:
            continue
        if abs(anchor-end) < max(1.5*endpoint_atr, .0025*abs(end)):
            continue
        # Both Fibonacci scenarios MUST share the same post-break impulse.
        fib_618 = end + .618*(anchor-end) if direction=='Short' else end-.618*(end-anchor)
        fib_786 = end + .786*(anchor-end) if direction=='Short' else end-.786*(end-anchor)
        recent_close=float(d.close.iloc[-1])
        confirmed_followup = d.close.iloc[endpoint.known+1:]
        # Invalidate permanently after a close beyond the origin or a confirmed
        # continuation past the anchored impulse endpoint, even on later reversal.
        if ((direction=='Short' and ((confirmed_followup > anchor+.15*a).any() or
                                     (confirmed_followup < end-.15*a).any())) or
            (direction=='Long' and ((confirmed_followup < anchor-.15*a).any() or
                                    (confirmed_followup > end+.15*a).any()))):
            continue
        if ((direction=='Short' and (recent_close > anchor+.15*a or recent_close < end-.15*a)) or
            (direction=='Long' and (recent_close < anchor-.15*a or recent_close > end+.15*a))):
            continue
        stop = (anchor + .15*a) if direction=='Short' else (anchor-.15*a)
        target = (end + .10*a) if direction=='Short' else (end-.10*a)
        against_higher = (h4=='Aufwärtstrend' if direction=='Short' else h4=='Abwärtstrend')
        against_middle = (h1=='Aufwärtstrend' if direction=='Short' else h1=='Abwärtstrend')
        aligned = (h4==h1==('Abwärtstrend' if direction=='Short' else 'Aufwärtstrend'))
        if against_higher or against_middle:
            higher='Gegen den bestätigten '+('/'.join(tf for tf,v in [('4H',against_higher),('1H',against_middle)] if v))+'-Trend'
        elif aligned:
            higher='4H und 1H stimmen bereits in dieser Richtung überein'
        else:
            higher='Übergeordnete Richtung unklar/uneinheitlich'
        cutoff=pd.Timestamp(d.time.iloc[endpoint.known])+pd.Timedelta(minutes=15)
        if cutoff.tzinfo is None:
            cutoff=cutoff.tz_localize('UTC')
        after_pivot = d.iloc[endpoint.known+1:]
        for ratio in levels:
            fib = fib_618 if ratio==.618 else fib_786
            if fib <= 0:
                continue
            geometry = (0 < target < fib < stop) if direction=='Short' else (0 < stop < fib < target)
            if not geometry:
                continue
            crv = abs(target-fib)/abs(fib-stop)
            # A closed 15M candle must test the level in the last 8 bars.
            # A long-past touch cannot remain a 'fresh' setup.
            touch = any(float(r.low) <= fib+fib_tolerance_atr*a and
                        float(r.high) >= fib-fib_tolerance_atr*a
                        for r in list(after_pivot.itertuples())[-8:])
            # If the 61.8% level has been breached on a 15M CLOSE, it is no
            # longer advertised as a fresh 61.8%-rejection. The deeper 78.6%
            # case is evaluated independently; no synthetic signal is generated.
            shallow_overrun = (ratio==.618 and (
                (direction=='Short' and (after_pivot.close > fib+.10*a).any()) or
                (direction=='Long' and (after_pivot.close < fib-.10*a).any())))
            res['checked'] += 1
            sr, sr_present = _zone_match(zones, fib, a, direction, ratio)
            atr_assessment = assess_target(direction=direction, entry=fib, stop=stop, target=target,
                                           atr=a, horizon_bars=horizon_bars, zones=zones,
                                           higher_trend=h4, middle_trend=h1)
            fvg = _fvg_near(d, direction, fib, a, i)
            reject = _five_min_rejection(d5,direction,fib,a,cutoff) if touch and not shallow_overrun else False
            level_name = '61,8' if ratio==.618 else '78,6'
            if shallow_overrun:
                state='61,8 % ÜBERLAUFEN · TIEFEREN RÜCKLAUF PRÜFEN'
                need='Abgeschlossener 15M-Kurs jenseits 61,8 %; 61,8 % liefert keine frische Einstiegsbestätigung.'
            elif not touch:
                state=f'RÜCKLAUF ZU {level_name} % BEOBACHTEN'
                need=f'Abgeschlossene Kerze am {level_name}-%-Bereich und anschließende 5M-Abweisung fehlen.'
            elif not reject:
                state=f'{level_name} % GETESTET · REAKTION ABWARTEN'
                need=f'Eine abgeschlossene 5M-Kerze mit Abweisung des {level_name}-%-Bereichs fehlt.'
            elif crv < minimum_rrr:
                state='REAKTION · CRV UNZUREICHEND'
                need='Mindest-Brutto-CRV nicht erreicht; Ziel nicht künstlich verlängern.'
            elif not sr_present:
                state='REAKTION · KEINE S/R-BESTÄTIGUNG'
                need='Reaktion sichtbar, aber am Fibonacci-Level fehlt die bestätigte gleichgerichtete S/R-Zone.'
            elif atr_assessment['status'] != 'KEIN UNMITTELBARER WARNHINWEIS':
                state='REAKTION · ATR-/ZONEN-WARNHINWEIS'
                need='Zielentfernung, gegenläufige Zone oder ATR-Zeithorizont zusätzlich prüfen.'
            elif aligned:
                state='TECHNISCHER 5M-TRIGGER (KEINE ORDER)'
                need='Brokerkurs/Handelszeit/News/Ausführung ungeprüft. 5M-Muster ist eine Heuristik.'
            else:
                state='GEGENTREND / ÜBERGANG · REAKTION BEOBACHTET'
                need='Kurzfristige Reaktion; 1H/4H noch nicht in neuer Richtung bestätigt. Nur Beobachtung.'
            warning=('CHoCH ist eine erste Strukturwarnung, keine bestätigte Gesamt-Trendumkehr. '
                     '61,8 und 78,6 % sind alternative Szenarien desselben Impulses, KEINE unabhängigen Signale. '
                     'Retracement-Level besitzen keine belegte eigenständige Trefferquote. '
                     'Gewinn/Verlust ohne Handelskosten; Slippage und Gaps nicht berücksichtigt.')
            res['ideas'].append(ReversalIdea(direction=direction,kind=kind,state=state,
                            break_level=break_level,broken_at_utc=str(pd.Timestamp(d.time.iloc[i])+pd.Timedelta(minutes=15)),
                            impulse_start=anchor,impulse_end=end,fib_786=fib_786,
                            fib_618=fib_618,fib_ratio=ratio,zone_tested=bool(touch),
                            entry_reference=fib,stop_reference=stop,target_reference=target,
                            gross_rrr=crv,atr15=a,stop_atr=abs(fib-stop)/a,target_atr=abs(target-fib)/a,
                            sr_confluence=sr,fvg_context=fvg,higher_tf_context=higher,
                            confirmation=need, warning=warning, target_context=atr_assessment['status']))
    if not res['ideas']:
        res['reason']=('Strukturbruch erkannt, aber das neue Impulsende ist noch nicht bestätigt.'
                       if res['early_breaks'] else
                       'Kein vollständig bestätigter 15M-Strukturbruch mit passendem Impuls in der geprüften Historie.')
    res['ideas'].sort(key=lambda x:(0 if 'TRIGGER' in x.state else 1,
                                    0 if 'BEOBACHTET' in x.state else 1,
                                    -min(x.gross_rrr,4)))
    return res
