"""Descriptive, causal sweep/reclaim/impulse events on already confirmed zones.

No order-book observation, backtested probabilities, or trading instructions.
Detection and confirmation use CLOSED bars only. Does not change engine.py zones.
"""
import numpy as np
import pandas as pd
from engine import zone_id

COLUMNS = ['Zonen_ID','Zeitebene','Typ','Seite','Untergrenze','Obergrenze',
           'Sweep_UTC','Rueckeroberung_UTC','Bestaetigung_UTC',
           'Sweep_Preis','Rueckeroberung_Preis','Impuls_Schwelle',
           'Durchbruch_ATR','Status','Begruendung']


def detect_zone_sweeps(d, zones, tf, reclaim_bars=3, confirm_bars=5,
                       min_depth_atr=.05, impulse_buffer_atr=.10, reference_bars=3):
    """One row per reclaimed sweep; candidate wicks alone are not events.

    A support sweep penetrates beneath its known lower boundary and reclaims
    above its known upper boundary (inverse for resistance). A following close
    past the previous reference highs/lows plus a buffer is an *impulse*, not
    proof of a new trend. Two consecutive adverse closes invalidate a zone.
    Historical events are not cancelled by later zone changes.

    Boundaries and ATR scale are frozen at zone confirmation, as in engine.py.
    The reference uses only candles strictly BEFORE the sweep itself.
    """
    if reclaim_bars < 0 or confirm_bars < 0 or reference_bars < 1:
        raise ValueError('Fenster müssen nichtnegative ganze Kerzenzahlen sein.')
    if min_depth_atr < 0 or impulse_buffer_atr < 0:
        raise ValueError('ATR-Schwellen dürfen nicht negativ sein.')
    results = []
    if len(d) == 0:
        return pd.DataFrame(columns=COLUMNS)
    highs = d.high.to_numpy(dtype=float)
    lows = d.low.to_numpy(dtype=float)
    closes = d.close.to_numpy(dtype=float)
    times = pd.to_datetime(d.time, utc=True).tolist()
    for z in zones:
        if z.known >= len(d)-1 or z.scale <= 0 or not np.isfinite(z.scale):
            continue
        support = z.side == 'support'
        edge_bad = z.low if support else z.high
        edge_good = z.high if support else z.low
        penetration = z.scale * min_depth_atr
        bad_limit = edge_bad + (-.1 if support else .1)*z.scale
        # Apply the exact two-consecutive-adverse-close rule BEFORE analyzing
        # events, so no apparent future sweep can resurrect a broken zone.
        first_break = len(d)
        streak = 0
        for k in range(z.known+1,len(d)):
            adverse = closes[k] < bad_limit if support else closes[k] > bad_limit
            streak = streak+1 if adverse else 0
            if streak >= 2:
                first_break = k
                break
        i = z.known+1
        while i < first_break:
            swept = lows[i] < edge_bad-penetration if support else highs[i] > edge_bad+penetration
            if not swept:
                i += 1
                continue
            sweep_idx = i
            ref_start = max(0, sweep_idx-reference_bars)
            # A pre-sweep extremum is causal and cannot use a future candle.
            reference = max(highs[ref_start:sweep_idx]) if support else min(lows[ref_start:sweep_idx])
            impulse_level = max(edge_good,reference)+impulse_buffer_atr*z.scale if support else min(edge_good,reference)-impulse_buffer_atr*z.scale
            reclaim_idx = None
            last_candidate = min(first_break-1,sweep_idx+reclaim_bars)
            for j in range(sweep_idx,last_candidate+1):
                reclaimed = closes[j] > edge_good if support else closes[j] < edge_good
                if reclaimed:
                    reclaim_idx = j
                    break
            if reclaim_idx is None:
                # A later penetration can still reclaim within its own window.
                i += 1
                continue
            confirm_idx = None
            deadline = min(first_break-1,reclaim_idx+confirm_bars)
            for j in range(reclaim_idx,deadline+1):
                beyond_impulse = closes[j] > impulse_level if support else closes[j] < impulse_level
                if beyond_impulse:
                    confirm_idx = j
                    break
            if confirm_idx is not None:
                phase = 'Impuls bestätigt'
            elif first_break < len(d) and first_break <= reclaim_idx+confirm_bars:
                phase = 'Nachträglich gebrochen'
            elif reclaim_idx+confirm_bars >= len(d):
                phase = 'Rückeroberung erkannt'
            else:
                phase = 'Kein Folgeimpuls'
            results.append({
                'Zonen_ID':zone_id(z), 'Zeitebene':tf,'Typ':z.kind,
                'Seite':'Long-Reaktion' if support else 'Short-Reaktion',
                'Untergrenze':float(z.low),'Obergrenze':float(z.high),
                'Sweep_UTC':times[sweep_idx],
                'Rueckeroberung_UTC':times[reclaim_idx],
                'Bestaetigung_UTC':times[confirm_idx] if confirm_idx is not None else pd.NaT,
                'Sweep_Preis':float(lows[sweep_idx] if support else highs[sweep_idx]),
                'Rueckeroberung_Preis':float(closes[reclaim_idx]),
                'Impuls_Schwelle':float(impulse_level),
                'Durchbruch_ATR':round(float((edge_bad-lows[sweep_idx] if support else highs[sweep_idx]-edge_bad)/z.scale),3),
                'Status':phase,
                'Begruendung':'Historischer Kursdurchbruch mit Schluss zurück jenseits der Zone; '
                              'Impuls = Schluss jenseits der Vor-Sweep-Extrema plus ATR-Puffer. '
                              'Keine Bestätigung institutioneller Orders oder Trendwende.'
            })
            # A new sweep may start after the confirmation/check window, not inside it.
            i = deadline+1
    return pd.DataFrame(results,columns=COLUMNS)


def detect_all_sweeps(frames, zones_by_tf):
    """Convenience collector; caller can append candle end times from TF."""
    from engine import TF
    parts = []
    for tf, d in frames.items():
        ev = detect_zone_sweeps(d,zones_by_tf[tf],tf)
        if not ev.empty:
            delta = pd.Timedelta(minutes=TF[tf][1])
            for col in ('Sweep_UTC','Rueckeroberung_UTC','Bestaetigung_UTC'):
                ev[col] = ev[col]+delta
            parts.append(ev)
    return pd.concat(parts,ignore_index=True) if parts else pd.DataFrame(columns=COLUMNS)
