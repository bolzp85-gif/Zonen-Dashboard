"""Display-only helpers; original zone detection and broker adapter stay unchanged."""
import pandas as pd


def candle_table(d, minutes):
    start = pd.to_datetime(d['time'],utc=True)
    end = start + pd.Timedelta(minutes=minutes)
    out = pd.DataFrame({
        'Beginn_UTC':start.dt.strftime('%Y-%m-%d %H:%M %z'),
        'Schluss_UTC':end.dt.strftime('%Y-%m-%d %H:%M %z'),
        'Beginn_Berlin':start.dt.tz_convert('Europe/Berlin').dt.strftime('%Y-%m-%d %H:%M %z'),
        'Schluss_Berlin':end.dt.tz_convert('Europe/Berlin').dt.strftime('%Y-%m-%d %H:%M %z'),
        'Open':d.open,'High':d.high,'Low':d.low,'Close':d.close,
    })
    return out.reset_index(drop=True)


def bundle_zones(zones, price):
    """Same-side groups with a common price intersection, not transitive chains.

    Bounds are the union of group members; known time is max(member known).
    This is a current-view grouping, not a historical zone state/backtest.
    No averaging or summation of scores, tests or events.
    """
    if zones.empty:
        return pd.DataFrame()
    groups = []
    source = zones[zones.Status != 'Gebrochen'].sort_values(
        ['Seite','Untergrenze','Obergrenze','Bekannt_ab','Zeitebene','Typ'],kind='stable')
    for _,row in source.iterrows():
        r = row.to_dict()
        compatible = [g for g in groups if g['side']==r['Seite'] and
                      max(g['common_low'],r['Untergrenze']) <= min(g['common_high'],r['Obergrenze'])]
        if compatible:
            g = compatible[-1]
            g['members'].append(r)
            g['common_low'] = max(g['common_low'],r['Untergrenze'])
            g['common_high'] = min(g['common_high'],r['Obergrenze'])
        else:
            groups.append({'side':r['Seite'],'common_low':r['Untergrenze'],
                           'common_high':r['Obergrenze'],'members':[r]})
    rows=[]
    for g in groups:
        m=g['members']
        lo,hi=min(r['Untergrenze'] for r in m),max(r['Obergrenze'] for r in m)
        rows.append({'Seite':g['side'],'Untergrenze':lo,'Obergrenze':hi,
                     'Zeitebenen':', '.join(t for t in ['1D','4H','1H','15Min','5Min'] if any(r['Zeitebene']==t for r in m)),
                     'Typen':', '.join(sorted({r['Typ'] for r in m})),
                     'Einzelzonen':len(m),'Bekannt_ab':max(pd.Timestamp(r['Bekannt_ab']) for r in m),
                     'Abstand_Preis':max(lo-price,price-hi,0),
                     'Punkte_Min':min(r['Strukturpunkte'] for r in m),
                     'Punkte_Max':max(r['Strukturpunkte'] for r in m),
                     'Lage':'Unter Kurs' if hi<price else ('Über Kurs' if lo>price else 'Am Kurs'),
                     'Details':m})
    out=pd.DataFrame(rows)
    if out.empty:
        return out
    out=out.sort_values(['Abstand_Preis','Untergrenze','Seite'],kind='stable').reset_index(drop=True)
    out.insert(0,'Zone',[f'Z{i+1}' for i in range(len(out))])
    return out


def nearest_balanced(groups, count):
    """Reserve space for both sides of the price, then fill by distance."""
    if groups.empty:
        return groups
    chosen=[]
    for pos in ['Am Kurs','Unter Kurs','Über Kurs']:
        candidates=groups[groups.Lage==pos]
        if not candidates.empty and len(chosen)<count:
            chosen.append(candidates.index[0])
    for idx in groups.index:
        if idx not in chosen and len(chosen)<count:
            chosen.append(idx)
    return groups.loc[chosen].sort_values('Abstand_Preis',kind='stable')


def price_text(value):
    return f'{value:,.5f}'.rstrip('0').rstrip('.').replace(',','_').replace('.',',').replace('_','.')


def candle_fingerprint(d):
    """SHA-256 of ordered UTC timestamps and exact float OHLC values."""
    import hashlib
    import json
    rows = [[pd.Timestamp(r.time).tz_convert('UTC').isoformat(),
             *[float(getattr(r, c)).hex() for c in ('open', 'high', 'low', 'close')]]
            for r in d.itertuples()]
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()


def zone_export_view(zones, price, reference_time, atr_by_tf):
    """One reference price; each zone retains its own timeframe's ATR."""
    out = zones.copy()
    out['Abstand_Preis'] = [max(r.Untergrenze-price, price-r.Obergrenze, 0)
                            for r in out.itertuples()]
    out['ATR_14_TF'] = out.Zeitebene.map(atr_by_tf)
    out['Abstand_ATR'] = (out.Abstand_Preis / out.ATR_14_TF).round(2)
    out['Referenzpreis'] = price
    out['Referenzschluss_UTC'] = reference_time
    active = out[out.Status != 'Gebrochen']
    out['Überlappung_TF'] = [
        '' if r.Status == 'Gebrochen' else ', '.join(
            tf for tf in atr_by_tf if tf != r.Zeitebene and
            ((active.Zeitebene == tf) & (active.Seite == r.Seite) &
             (active.Untergrenze <= r.Obergrenze) &
             (active.Obergrenze >= r.Untergrenze)).any())
        for r in out.itertuples()]
    return out
