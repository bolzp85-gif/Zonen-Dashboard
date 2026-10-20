"""FRED-only descriptive macro compass; no trading signal or past-data backtest.

Latest observation date is NOT release time. The two are deliberately separated.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
import math
import requests

SERIES = {
    'DFF': ('Fed Funds effektiv', 'täglich', 10),
    'DFII10': ('US-Realrendite 10J', 'täglich', 10),
    'T10YIE': ('Breakeven-Inflation 10J', 'täglich', 10),
    'CPIAUCSL': ('US-Verbraucherpreise CPI', 'monatlich', 75),
    'UNRATE': ('US-Arbeitslosenquote', 'monatlich', 75),
    'ICSA': ('US-Erstanträge Arbeitslosenhilfe', 'wöchentlich', 22),
    'BAMLH0A0HYM2': ('US-High-Yield-Spread', 'täglich', 10),
    'VIXCLS': ('US-VIX (Cboe)', 'täglich', 10),
    'NFCI': ('National Financial Conditions Index', 'wöchentlich', 22),
}


def parse_series(payload: dict, name: str, today: date | None = None) -> dict:
    if today is None:
        today = datetime.now(timezone.utc).date()
    if name not in SERIES:
        raise ValueError('FRED-Datenreihe ist nicht zugelassen')
    title, freq, allowed_age = SERIES[name]
    observations = payload.get('observations') if isinstance(payload, dict) else None
    if not isinstance(observations, list):
        raise ValueError('FRED-Antwort hat keine observations-Liste')
    available=[]
    for obs in observations:
        if not isinstance(obs,dict): continue
        try:
            dt=date.fromisoformat(obs['date'])
            v=float(obs['value'])
            if math.isfinite(v) and dt <= today:
                available.append((dt,v))
        except (ValueError,TypeError,KeyError):
            continue
    available.sort(key=lambda x:x[0])
    if not available:
        raise ValueError('Keine gültigen FRED-Beobachtungen')
    latest=available[-1]
    age=(today-latest[0]).days
    # CPI is an index: YoY change, NOT the raw index or monthly rate.
    if name == 'CPIAUCSL':
        previous=next((v for dt,v in reversed(available[:-1]) if dt.year==latest[0].year-1 and dt.month==latest[0].month), None)
        value=(latest[1]/previous-1)*100 if previous and previous>0 else None
        unit='% gegenüber Vorjahr'
        comparison=None
    else:
        value=latest[1]
        unit='%' if name in ('DFF','DFII10','T10YIE','UNRATE','BAMLH0A0HYM2') else ('Indexpunkte' if name=='VIXCLS' else 'Index' if name=='NFCI' else 'Anträge')
        comparison=(latest[1]-available[-2][1]) if len(available)>=2 else None
    trend=('steigend' if comparison > 1e-8 else 'fallend' if comparison < -1e-8 else 'stabil') if comparison is not None else 'k. A.'
    # Weekly ICSA: compare latest four releases with the prior four releases.
    # This is a current trend descriptor, NOT a backtested predictive signal.
    change_4w_pct = None
    if name == 'ICSA' and len(available) >= 8:
        recent = available[-4:]
        previous = available[-8:-4]
        # Avoid misrepresenting sparse historical points as consecutive weeks.
        valid_intervals = all(5 <= (available[i][0]-available[i-1][0]).days <= 9
                              for i in range(len(available)-7, len(available)))
        if valid_intervals:
            base = sum(v for _,v in previous) / 4.0
            if base > 0:
                change_4w_pct = ((sum(v for _,v in recent)/4.0)/base - 1) * 100.0
    return {'id':name,'name':title,'frequency':freq,'date':latest[0].isoformat(),
            'raw_value':latest[1],'value':value,'unit':unit,
            'change':comparison,'change_4w_pct':change_4w_pct,'trend':trend,'age_days':age,
            'status':'AKTUELL' if age <= allowed_age else 'VERALTET',
            'remark':'Beobachtungsdatum, nicht Veröffentlichungszeitpunkt. Revisionen möglich.'}


def _checked_at_utc() -> str:
    """When THIS FRED request was attempted; not a live uptime monitor."""
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def get_series(key: str, series_id: str, timeout: int = 9) -> dict:
    if series_id not in SERIES:
        raise ValueError('Nicht zugelassene FRED-Reihe')
    checked_at = _checked_at_utc()
    if not str(key).strip():
        return {'id': series_id, 'name': SERIES[series_id][0], 'status': 'FEHLER',
                'fetch_status': 'KEIN API-KEY', 'checked_at_utc': checked_at,
                'remark': 'FRED_API_KEY fehlt', 'value': None, 'date': None}
    try:
        r = requests.get('https://api.stlouisfed.org/fred/series/observations', params={
            'series_id': series_id, 'api_key': key, 'file_type': 'json',
            'sort_order': 'desc', 'limit': 28,
        }, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as exc:
        # Sanitized response; NEVER show exception text (it could include the API key).
        status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
        explanation = (f'HTTP {status_code}' if status_code else type(exc).__name__)
        return {'id':series_id,'name':SERIES[series_id][0], 'status':'FEHLER',
                'fetch_status':'ABRUF FEHLGESCHLAGEN', 'checked_at_utc':checked_at,
                'remark':f'FRED-Anfrage fehlgeschlagen ({explanation}). Kein Nachweis eines FRED-Ausfalls.',
                'date':None,'value':None}
    try:
        result = parse_series(r.json(), series_id)
    except (ValueError, TypeError, KeyError):
        return {'id':series_id,'name':SERIES[series_id][0], 'status':'FEHLER',
                'fetch_status':'ONLINE', 'checked_at_utc':checked_at,
                'remark':'HTTP-Antwort empfangen, aber keine auswertbaren Beobachtungen.',
                'date':None,'value':None}
    return {**result, 'fetch_status':'ONLINE', 'checked_at_utc':checked_at}


def _failed_record(series_id: str, reason: str = 'Unbekannter Abruffehler') -> dict:
    return {'id':series_id,'name':SERIES[series_id][0],'status':'FEHLER',
            'fetch_status':'ABRUF FEHLGESCHLAGEN','checked_at_utc':_checked_at_utc(),
            'remark':reason, 'date':None,'value':None}


def snapshot(key: str) -> list[dict]:
    if not str(key).strip():
        return [get_series('', sid) for sid in SERIES]
    found={}
    # Parallelism bound to four connections; cache is applied at UI layer.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(get_series,key,k):k for k in SERIES}
        for fut in as_completed(futures):
            k=futures[fut]
            try:
                found[k]=fut.result()
            except Exception:
                found[k]=_failed_record(k)
    return [found[k] for k in SERIES]


def health_summary(records: list[dict] | None) -> dict:
    """Separate request reachability from data age and scored coverage.

    ONLINE means successful LAST request, not that FRED is currently up.
    An HTTP 200 with invalid/old data may be ONLINE and still unusable.
    """
    records = records or []
    total = len(SERIES)
    by_id = {r.get('id'): r for r in records if isinstance(r, dict)}
    healthy = current = outdated = errors = 0
    rows = []
    for sid, (name, frequency, max_age) in SERIES.items():
        r = by_id.get(sid, {})
        fetch = r.get('fetch_status', 'NICHT ABGERUFEN')
        data = r.get('status', 'FEHLT')
        if fetch == 'ONLINE': healthy += 1
        # Some rows need extra history (ICSA trend, CPI year-over-year).
        # They can be reachable and dated yet unusable for the intended purpose.
        usable = (r.get('value') is not None and
                  (sid != 'ICSA' or r.get('change_4w_pct') is not None))
        display_data = data if data != 'AKTUELL' or usable else 'UNVOLLSTÄNDIG'
        if fetch == 'ONLINE' and display_data == 'AKTUELL': current += 1
        if display_data == 'VERALTET': outdated += 1
        if fetch != 'ONLINE' or display_data in ('FEHLER','FEHLT','UNVOLLSTÄNDIG'): errors += 1
        rows.append({'id':sid,'name':name,'fetch_status':fetch,
                     'data_status':display_data,'checked_at_utc':r.get('checked_at_utc'),
                     'observation_date':r.get('date'),'age_days':r.get('age_days'),
                     'max_age_days':max_age, 'reason':r.get('remark', '')})
    return {'total':total,'online':healthy,'current':current,'outdated':outdated,
            'unavailable':errors,'rows':rows,
            'last_checked_utc':max((r['checked_at_utc'] for r in rows if r['checked_at_utc']),default=None),
            'all_current':current==total}


def context_text(symbol: str) -> str:
    s=symbol.strip().upper().replace('/','')
    if any(k in s for k in ('GOLD','XAU')):
        return 'Gold: Realrendite, Dollar- und Kreditstress beachten. Keine automatische Long-/Short-Ableitung.'
    if any(k in s for k in ('OIL','WTI','CRUDE')):
        return 'Öl: US-Konjunktur ist nur ein Teilbild; OPEC, Bestände und Geopolitik fehlen in FRED-Kernreihen.'
    if any(k in s for k in ('EURUSD','USDJPY','USDCHF')):
        return 'FX: US-Daten allein reichen nicht; Zinspolitik und Daten der Gegenwährung fehlen.'
    return 'US-Indizes: Realzinsen, Beschäftigung und Kreditspreads beschreiben Rahmenbedingungen, kein Einstiegssignal.'
