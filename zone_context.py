"""Descriptive indicator context on closed candles; no scoring or execution."""
import numpy as np
import pandas as pd
from engine import atr

EMA_PERIODS = (10, 20, 50, 200)


def indicator_series(d, stoch_length=8, smooth_k=3, smooth_d=3):
    if any(not isinstance(n, int) or n < 1 for n in (stoch_length, smooth_k, smooth_d)):
        raise ValueError('Stochastik-Perioden müssen positive ganze Zahlen sein.')
    out = pd.DataFrame(index=d.index)
    for n in EMA_PERIODS:
        # Recursive EMA, seeded with the first available close. No backfill.
        out[f'EMA_{n}'] = d.close.ewm(span=n, adjust=False, min_periods=n).mean()
    low = d.low.rolling(stoch_length, min_periods=stoch_length).min()
    high = d.high.rolling(stoch_length, min_periods=stoch_length).max()
    raw = 100 * (d.close-low) / (high-low).replace(0, np.nan)
    out['Stoch_K'] = raw.rolling(smooth_k, min_periods=smooth_k).mean()
    out['Stoch_D'] = out.Stoch_K.rolling(smooth_d, min_periods=smooth_d).mean()
    out['ATR_14'] = atr(d)
    return out


def indicator_summary(d, indicators, minutes):
    last = indicators.iloc[-1]
    k, signal = last.Stoch_K, last.Stoch_D
    valid = np.isfinite(k) and np.isfinite(signal)
    location = ('Oberhalb 80' if k > 80 else 'Unterhalb 20' if k < 20 else '20–80') if valid else 'Nicht verfügbar'
    relation = ('K > D' if k > signal else 'K < D' if k < signal else 'K = D') if valid else 'Nicht verfügbar'
    cross = 'Keine Kreuzung in letzter Kerze'
    if not valid:
        cross = 'Nicht verfügbar'
    elif len(indicators) > 1:
        prev = indicators.iloc[-2]
        if not np.isfinite(prev.Stoch_K) or not np.isfinite(prev.Stoch_D):
            cross = 'Nicht verfügbar'
        elif prev.Stoch_K <= prev.Stoch_D and k > signal:
            cross = 'K kreuzt über D'
        elif prev.Stoch_K >= prev.Stoch_D and k < signal:
            cross = 'K kreuzt unter D'
    result = {name: float(last[name]) for name in indicators.columns}
    result.update(Kontextschluss_UTC=pd.Timestamp(d.time.iloc[-1])+pd.Timedelta(minutes=minutes),
                  Stoch_Bereich=location, Stoch_Relation=relation, Stoch_Kreuzung=cross,
                  Historienkerzen=len(d),
                  EMA_200_Hinweis=('Nicht verfügbar: unter 200 Kerzen' if len(d) < 200 else
                                   'Begrenzter Vorlauf: unter 600 Kerzen' if len(d) < 600 else
                                   'Mindestens 600 Kerzen; abhängig vom Startwert'))
    return result


def add_zone_context(zones, summaries, near_atr=0.25):
    """Use each zone's own TF, keeping original fields and points intact."""
    if not np.isfinite(near_atr) or near_atr < 0:
        raise ValueError('EMA-Nähe muss eine nichtnegative ATR-Distanz sein.')
    out = zones.copy()
    fields = ['Kontextschluss_UTC', 'Stoch_K', 'Stoch_D', 'Stoch_Bereich',
              'Stoch_Relation', 'Stoch_Kreuzung', 'EMA_200_Hinweis']
    for name in fields:
        out[name] = out.Zeitebene.map({tf: s[name] for tf, s in summaries.items()})
    out['Zonenbreite_ATR'] = (out.Obergrenze-out.Untergrenze) / out.ATR_14_TF
    out['EMA_Nähe_ATR_Schwelle'] = near_atr
    matches = {idx: [] for idx in out.index}
    for n in EMA_PERIODS:
        name = f'EMA_{n}'
        out[name] = out.Zeitebene.map({tf: s[name] for tf, s in summaries.items()})
        distances, labels = [], []
        for idx, row in out.iterrows():
            value, scale = row[name], row.ATR_14_TF
            if not np.isfinite(value) or not np.isfinite(scale) or scale <= 0:
                distances.append(np.nan)
                labels.append('Nicht verfügbar')
                continue
            distance = max(row.Untergrenze-value, value-row.Obergrenze, 0) / scale
            distances.append(distance)
            label = 'In Zone' if distance == 0 else 'Nahe Zone' if distance <= near_atr else 'Außerhalb Nähebereich'
            labels.append(label)
            if row.Status != 'Gebrochen' and label in ('In Zone', 'Nahe Zone'):
                matches[idx].append(f'EMA {n}: {label}')
        out[f'{name}_Abstand_ATR'] = distances
        out[f'{name}_Lage'] = labels
    out['EMA_Zonenkontext'] = [
        'Gebrochen – kein aktiver Zonenkontext' if row.Status == 'Gebrochen' else
        '; '.join(matches[idx]) if matches[idx] else 'Keine verfügbare EMA im Nähebereich'
        for idx, row in out.iterrows()]
    return out
