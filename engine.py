"""Causal, descriptive zone heuristics. No execution or predictive probabilities."""
from dataclasses import dataclass
import numpy as np
import pandas as pd

TF = {'1D': ('OneDay', 1440), '4H': ('FourHours', 240), '1H': ('OneHour', 60), '15Min': ('FifteenMinutes', 15), '5Min': ('FiveMinutes', 5)}


def clean_candles(frame, minutes, now=None):
    d = frame.copy()
    required = ['time', 'open', 'high', 'low', 'close']
    if not set(required).issubset(d.columns):
        raise ValueError('Kerzenformat unvollständig: time/open/high/low/close erforderlich.')
    d['time'] = pd.to_datetime(d.time, utc=True, errors='coerce')
    for c in required[1:]:
        d[c] = pd.to_numeric(d[c], errors='coerce')
    if d[required].isna().any().any() or not np.isfinite(d[required[1:]].to_numpy()).all():
        raise ValueError('Ungültige Zeit- oder Kurswerte; Analyse angehalten.')
    bad = (d.high < d[['open','close','low']].max(axis=1)) | (d.low > d[['open','close','high']].min(axis=1))
    if bad.any():
        raise ValueError('Widersprüchliche OHLC-Werte; Analyse angehalten.')
    dupe = d[d.time.duplicated(False)]
    if len(dupe) and (dupe.groupby('time')[required[1:]].nunique() > 1).any().any():
        raise ValueError('Widersprüchliche doppelte Kerzen.')
    d = d.drop_duplicates('time').sort_values('time')
    now = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        now = now.tz_localize('UTC')
    d = d[d.time + pd.Timedelta(minutes=minutes) <= now].reset_index(drop=True)
    return d


def atr(d, length=14):
    prev = d.close.shift()
    tr = pd.concat([d.high-d.low, (d.high-prev).abs(), (d.low-prev).abs()], axis=1).max(axis=1)
    return tr.rolling(length, min_periods=length).mean()


@dataclass(frozen=True)
class Zone:
    kind: str
    side: str
    low: float
    high: float
    origin: int
    known: int
    scale: float
    strength: float
    reason: str


def zone_id(z):
    """Stable deterministic fingerprint for one confirmed zone in its source timeframe."""
    return '|'.join((z.kind, z.side, str(z.origin), str(z.known), float(z.low).hex(), float(z.high).hex()))


def _break_index(d, z):
    """First confirmed break after the zone became known (closed bars only)."""
    adverse = 0
    for k in range(z.known + 1, len(d)):
        close = float(d.close.iloc[k])
        crossed = (close < z.low - .1*z.scale if z.side == 'support'
                   else close > z.high + .1*z.scale)
        adverse = adverse + 1 if crossed else 0
        if adverse == 2:
            return k
    return None


def _role_reversal(d, z, retest_window=25, confirm_window=3):
    """Optional S/R role swap, only after a confirmed close break AND retest.

    Retest and rejection must be observable in *closed* candles. A new zone is
    created at confirmation, never backdated to the original break.
    """
    broken_at = _break_index(d, z)
    if broken_at is None:
        return None
    new_side = 'resistance' if z.side == 'support' else 'support'
    last = min(len(d) - 1, broken_at + retest_window)
    # An actual retest requires the market to first leave the old zone.
    # Without this, a follow-through breakdown candle may be mistaken for
    # an independent return to the former S/R boundary.
    broken_bar = d.iloc[broken_at]
    departed = (broken_bar.high < z.low if new_side == 'resistance'
                else broken_bar.low > z.high)
    for k in range(broken_at + 1, last + 1):
        bar = d.iloc[k]
        if not departed:
            if new_side == 'resistance' and bar.high < z.low:
                departed = True
            elif new_side == 'support' and bar.low > z.high:
                departed = True
            continue
        touched = bar.low <= z.high and bar.high >= z.low
        if not touched:
            continue
        limit = min(len(d) - 1, k + confirm_window - 1)
        for j in range(k, limit + 1):
            chunk = d.iloc[k:j+1]
            if new_side == 'support':
                invalid = (chunk.close < z.low - .1*z.scale).any()
                confirmed = float(d.close.iloc[j]) > z.high + .1*z.scale
            else:
                invalid = (chunk.close > z.high + .1*z.scale).any()
                confirmed = float(d.close.iloc[j]) < z.low - .1*z.scale
            if invalid:
                break
            if confirmed:
                strength = abs(float(d.close.iloc[j]) - (z.high if new_side == 'support' else z.low)) / z.scale
                return Zone('Unterstützung/Widerstand', new_side, z.low, z.high,
                            k, j, z.scale, strength,
                            'Bestätigter Rollenwechsel: zwei Bruch-Schlusskurse, Rücklauf und Abweisung')
    return None


def discover(d, swing=3):
    """Only repeatedly confirmed S/R levels; no speculative liquidity zones.

    One swing is merely a reference. On the SECOND independent similar swing,
    a zone becomes known. Bounds freeze at that time. Subsequent similar
    swings are reactions, not fresh, overlapping zones. Genuinely renewed
    levels can form only after a previous zone was broken.
    """
    if swing < 1:
        raise ValueError('Swing-Fenster muss positiv sein.')
    a = atr(d).to_numpy()
    zones, pivots = [], {'support': [], 'resistance': []}
    for i in range(max(14, 2*swing), len(d)):
        p = i - swing
        if p < max(14, swing) or not np.isfinite(a[p]) or a[p] <= 0:
            continue
        for side, col, fn in [('support', 'low', min), ('resistance', 'high', max)]:
            window = d[col].iloc[p-swing:i+1].to_numpy()
            value = float(d[col].iloc[p])
            if value != fn(window) or np.count_nonzero(window == value) != 1:
                continue
            scale = float(a[p])
            # Independent contacts must be separated by at least 2*swing+1 bars.
            # Match tolerance is ATR-scaled, not an arbitrary price threshold.
            previous = [x for x in pivots[side]
                        if p-x[0] >= 2*swing+1 and abs(x[1]-value) <= .30*scale]
            if previous:
                q, earlier = previous[-1]
                lo, hi = min(earlier,value)-.16*scale, max(earlier,value)+.16*scale
                # Do not create a second zone while a matching existing S/R
                # level is still active, even after a third / fourth test.
                active_match = any(z.side == side and
                                   max(z.low,lo) <= min(z.high,hi) and
                                   _break_index(d.iloc[:i+1],z) is None
                                   for z in zones)
                # A prior historical level that was already broken between
                # the two pivots must not validate a new zone by itself.
                closes = d.close.iloc[q+swing+1:i+1]
                if side == 'support':
                    breached = closes < lo - .1*scale
                else:
                    breached = closes > hi + .1*scale
                invalid_before_confirm = bool((breached & breached.shift(fill_value=False)).any())
                if not active_match and not invalid_before_confirm:
                    initial_reaction = abs(float(d.close.iloc[i])-value)/scale
                    zones.append(Zone('Unterstützung/Widerstand',side,lo,hi,q,i,scale,
                                      initial_reaction,
                                      'Zwei unabhängige bestätigte Swing-' +
                                      ('Tiefs (Unterstützung)' if side=='support' else 'Hochs (Widerstand)') +
                                      '; ±0,16 ATR um die Extrempunkte'))
            pivots[side].append((p,value))
    # Optional zone role change: no zone is reused until an observed retest.
    # Only original confirmed levels are used (no recursive chain of flips).
    reversals = []
    for z in zones:
        reverse = _role_reversal(d,z)
        if reverse is not None:
            matching = any(t.side == reverse.side and
                           max(t.low,reverse.low) <= min(t.high,reverse.high) and
                           t.known <= reverse.known and
                           _break_index(d.iloc[:reverse.known+1],t) is None
                           for t in zones + reversals)
            if not matching:
                reversals.append(reverse)
    return sorted(zones + reversals, key=lambda z:(z.known,z.origin,z.side,z.low))


def evaluate(d, z):
    """Current retrospective view; active only until first confirmed break."""
    after = d.iloc[z.known+1:]
    tests, touching, adverse, broken = 0, False, 0, False
    latest, last_event_time = 'Keine neue Reaktion', None
    for _, row in after.iterrows():
        touch = row.low <= z.high and row.high >= z.low
        if touch and not touching:
            tests += 1
        touching = bool(touch)
        is_beyond = (row.close < z.low-.1*z.scale if z.side == 'support'
                     else row.close > z.high+.1*z.scale)
        adverse = adverse+1 if is_beyond else 0
        event = None
        if adverse >= 2:
            broken = True
            event = 'Durchbruch bestätigt'
        elif touch:
            if z.side == 'resistance' and row.high > z.high and row.close < z.low:
                event = 'Rückkehr unter Widerstand'
            elif z.side == 'support' and row.low < z.low and row.close > z.high:
                event = 'Rückkehr über Unterstützung'
            else:
                event = 'Zone berührt'
        if event:
            latest, last_event_time = event, row.time
        if broken:
            break
    age = len(d)-1-z.known
    # Display heuristic only. Further meaningful rejections are evaluated in
    # zone_quality; no blanket penalty for a repeatedly confirmed S/R level.
    points = max(0, min(100, 43 + min(max(z.strength,0),3)*9 - min(age/22,18)))
    price = float(d.close.iloc[-1])
    distance = max(z.low-price,price-z.high,0)
    return {'Zonen_ID':zone_id(z), 'Typ':'Unterstützung' if z.side=='support' else 'Widerstand',
            'Seite':'Unterstützung' if z.side=='support' else 'Widerstand',
            'Untergrenze':z.low, 'Obergrenze':z.high,
            'Status':'Gebrochen' if broken else ('Getestet' if tests else 'Bestätigt'),
            'Strukturpunkte':round(min(points,20) if broken else points),
            'Basiskontakte':1 if z.reason.startswith('Bestätigter Rollenwechsel') else 2,
            'Formation':'Rollenwechsel' if z.reason.startswith('Bestätigter Rollenwechsel') else 'Mehrfachtest',
            'Tests':tests, 'Abstand_ATR':round(distance/float(atr(d).iloc[-1]),2),
            'Bekannt_ab':d.time.iloc[z.known], 'Letztes_Ereignis':latest,
            'Ereigniszeit':last_event_time, 'Begründung':z.reason}


def analyze(d):
    if len(d) < 40:
        raise ValueError('Mindestens 40 abgeschlossene Kerzen je Zeitebene erforderlich.')
    if not np.isfinite(atr(d).iloc[-1]) or atr(d).iloc[-1] <= 0:
        raise ValueError('Keine auswertbare Schwankungsbreite.')
    zones = discover(d)
    rows = [evaluate(d,z) for z in zones]
    # Range classification is an attribute of existing S/R levels, not a third zone.
    latest_atr = float(atr(d).iloc[-1])
    active_support = [j for j,(z,r) in enumerate(zip(zones,rows))
                      if z.side == 'support' and r['Status'] != 'Gebrochen']
    active_resistance = [j for j,(z,r) in enumerate(zip(zones,rows))
                         if z.side == 'resistance' and r['Status'] != 'Gebrochen']
    for si in active_support:
        for ri in active_resistance:
            lower, upper = zones[si], zones[ri]
            distance = upper.low - lower.high
            if not (.8*latest_atr <= distance <= 6*latest_atr):
                continue
            if abs(upper.known - lower.known) > 80:
                continue
            begin = max(0, len(d)-80, min(upper.origin,lower.origin))
            closes = d.close.iloc[begin:]
            if len(closes) < 8:
                continue
            contained = closes.between(lower.low-.2*latest_atr,
                                       upper.high+.2*latest_atr).mean()
            if contained >= .70:
                for index in (si,ri):
                    if rows[index]['Formation'] == 'Mehrfachtest':
                        rows[index]['Formation'] = 'Range-Grenze'
    return pd.DataFrame(rows)


def demo_data(count=400):
    """Coherent synthetic 5-minute path aggregated to all four timeframes."""
    rng = np.random.default_rng(42)
    n = (count+2)*288
    end = pd.Timestamp.now(tz='UTC').floor('D')
    times = pd.date_range(end=end-pd.Timedelta(minutes=5), periods=n, freq='5min')
    t = np.arange(n)
    close = 100+np.cumsum(rng.normal(0,.12,n))+1.4*np.sin(t/35)
    op = np.r_[close[0],close[:-1]]
    d = pd.DataFrame({'time':times,'open':op,'high':np.maximum(op,close)+rng.uniform(.02,.18,n),
                      'low':np.minimum(op,close)-rng.uniform(.02,.18,n),'close':close})
    return {tf:d.set_index('time').resample(f'{mins}min',origin='start_day').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna().reset_index().tail(count).reset_index(drop=True) for tf,(_,mins) in TF.items()}
