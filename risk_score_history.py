"""Historical risk-score *description*, not a forward-test or trading signal.

Uses actual overlapping daily HY/VIX observations; weekly NFCI is carried
forward only once its observation Friday is at least five calendar days old.
This is an APPROXIMATION of publication lag; historical revisions remain.
"""
from __future__ import annotations
from datetime import date, timedelta
from math import isfinite
from risk_regime import FACTORS, factor_score


def _history(record: dict) -> dict[date,float]:
    out = {}
    for item in record.get('history',[]) or []:
        try:
            d = date.fromisoformat(item['date'])
            v = float(item['value'])
            if isfinite(v): out[d] = v
        except (TypeError, ValueError, KeyError):
            continue
    return out


def describe(records: list[dict] | None, horizons=(5,10,20)) -> dict:
    byid={r.get('id'):r for r in (records or []) if isinstance(r,dict)}
    missing=[s for s in FACTORS if s not in byid or byid[s].get('status')!='AKTUELL']
    if missing:
        return {'rows':[],'changes':{},'reason':'Pflichtreihen nicht aktuell: '+', '.join(missing)}
    hist={sid:_history(byid[sid]) for sid in FACTORS}
    if any(len(hist[sid]) < 2 for sid in FACTORS):
        return {'rows':[],'changes':{},'reason':'Zu wenig historische FRED-Messwerte für einen Risk-Score-Verlauf.'}
    daily=sorted(set(hist['BAMLH0A0HYM2']) & set(hist['VIXCLS']))
    weekly=sorted(hist['NFCI'])
    rows=[]
    for d in daily:
        nfci_published=[w for w in weekly if w + timedelta(days=5) <= d]
        if not nfci_published: continue
        nfci=hist['NFCI'][nfci_published[-1]]
        raw={'BAMLH0A0HYM2':hist['BAMLH0A0HYM2'][d], 'VIXCLS':hist['VIXCLS'][d],'NFCI':nfci}
        score=sum(FACTORS[s]['weight']*factor_score(raw[s],FACTORS[s]['risk_on'],FACTORS[s]['risk_off'])
                  for s in FACTORS)
        rows.append({'date':d.isoformat(),'score':round(score,2)})
    if not rows:
        return {'rows':[],'changes':{},'reason':'Keine gemeinsamen historischen Datenpunkte einschließlich NFCI.'}
    changes={}
    for h in horizons:
        if len(rows) <= h:
            changes[int(h)] = None
        else:
            changes[int(h)] = {'previous_date': rows[-1-h]['date'],
                                'previous_score': rows[-1-h]['score'],
                                'current_score': rows[-1]['score'],
                                'delta': round(rows[-1]['score']-rows[-1-h]['score'],2)}
    return {'rows':rows,'changes':changes,'reason':None,
            'note':'Retrospektive Näherung aus heutigen FRED-Datenständen. NFCI ab Beobachtungstag +5 Tage angenähert; historische Freigabe- und Revisionszeitpunkte sind nicht exakt rekonstruiert. Keine Walk-Forward-Validierung.'}
