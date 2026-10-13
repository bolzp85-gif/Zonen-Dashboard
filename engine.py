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


def discover(d, swing=3):
    """known is the last CLOSED bar required to discover a zone. Bounds freeze then."""
    a = atr(d).to_numpy()
    zones, pivots = [], {'support': [], 'resistance': []}
    for i in range(14, len(d)):
        p = i-swing
        if p >= max(14, swing) and np.isfinite(a[p]) and a[p] > 0:
            for side, col, fn in [('support','low',min), ('resistance','high',max)]:
                window = d[col].iloc[p-swing:i+1].to_numpy()
                v = float(d[col].iloc[p])
                if v != fn(window) or np.count_nonzero(window == v) != 1:
                    continue
                scale = float(a[p])
                strength = abs(float(d.close.iloc[i])-v)/scale
                zones.append(Zone('Unterstützung/Widerstand',side,v-.18*scale,v+.18*scale,p,i,scale,strength,'Bestätigter Wendepunkt; 3 Folgekerzen'))
                previous = [x for x in pivots[side] if p-x[0] >= 2*swing+1 and abs(x[1]-v) <= .25*scale]
                if previous:
                    q, value = previous[-1]
                    zones.append(Zone('Liquidität vermutet',side,min(value,v)-.1*scale,max(value,v)+.1*scale,q,i,scale,strength,'Zwei ähnliche bestätigte Tiefs/Hochs; Stops nicht sichtbar'))
                pivots[side].append((p,v))
        # Compact 3-bar base followed by a displacement candle.
        if i >= 17 and np.isfinite(a[i-1]) and a[i-1] > 0:
            scale = float(a[i-1])
            base = d.iloc[i-3:i]
            lo, hi = float(base.low.min()), float(base.high.max())
            body = float(d.close.iloc[i]-d.open.iloc[i])
            if hi-lo <= 1.2*scale and abs(body) >= 1.2*scale:
                side = 'support' if body > 0 else 'resistance'
                outside = d.close.iloc[i] > hi+.3*scale if body > 0 else d.close.iloc[i] < lo-.3*scale
                if outside:
                    zones.append(Zone('Angebot/Nachfrage',side,lo,hi,i-3,i,scale,abs(body)/scale,'3-Kerzen-Basis mit deutlichem Impuls; verbleibende Orders unbekannt'))
    return zones


def evaluate(d, z):
    after = d.iloc[z.known+1:]
    tests, touching, beyond, broken = 0, False, 0, False
    latest = 'Keine neue Reaktion'
    last_event_time = None
    for idx, row in after.iterrows():
        touch = row.low <= z.high and row.high >= z.low
        if touch and not touching:
            tests += 1
        touching = touch
        is_beyond = row.close < z.low-.1*z.scale if z.side == 'support' else row.close > z.high+.1*z.scale
        beyond = beyond+1 if is_beyond else 0
        event = None
        if beyond >= 2:
            broken = True
            event = 'Durchbruch bestätigt'
        elif z.side == 'resistance' and row.high > z.high and row.close < z.low:
            event = 'Möglicher Sweep oberhalb'
        elif z.side == 'support' and row.low < z.low and row.close > z.high:
            event = 'Möglicher Sweep unterhalb'
        elif touch:
            event = 'Zone berührt'
        if event:
            latest, last_event_time = event, row.time
        if broken:
            break  # no silent resurrection: role reversal requires a separate future model
    age = len(d)-1-z.known
    points = max(0, min(100, 40+min(z.strength,3)*12 + (10 if z.kind != 'Unterstützung/Widerstand' else 0)-max(0,tests-1)*7-min(age/15,20)))
    price = float(d.close.iloc[-1])
    distance = max(z.low-price,price-z.high,0)
    return {'Typ':z.kind, 'Seite':'Unterstützung' if z.side=='support' else 'Widerstand',
            'Untergrenze':z.low, 'Obergrenze':z.high, 'Status':'Gebrochen' if broken else ('Getestet' if tests else 'Ungetestet'),
            'Strukturpunkte':round(points), 'Tests':tests, 'Abstand_ATR':round(distance/float(atr(d).iloc[-1]),2),
            'Bekannt_ab':d.time.iloc[z.known], 'Letztes_Ereignis':latest, 'Ereigniszeit':last_event_time,
            'Begründung':z.reason}


def analyze(d):
    if len(d) < 40:
        raise ValueError('Mindestens 40 abgeschlossene Kerzen je Zeitebene erforderlich.')
    if not np.isfinite(atr(d).iloc[-1]) or atr(d).iloc[-1] <= 0:
        raise ValueError('Keine auswertbare Schwankungsbreite.')
    rows = [evaluate(d,z) for z in discover(d)]
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
