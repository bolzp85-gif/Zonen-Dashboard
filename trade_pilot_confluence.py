"""v0.7.2.1 consistency-audited synthesis of independently generated planning scenarios.

Never turns matching price levels into a measured statistical edge or an order.
For countertrend, user preference requires a confirmed 1H direction change;
existing 4H+1H alignment rule is retained before a technical green trigger.
"""
from __future__ import annotations
from math import isfinite
from typing import Any

SEARCH_MODES=('Long','Short','Seitwärts')


def summarize(*, mode: str, trend_context: dict, zones_result: dict | None = None,
              fib_result: dict | None = None, range_result: dict | None = None,
              minimum_rrr: float=1.8, horizon_15m: int=8) -> dict:
    if mode not in SEARCH_MODES:
        raise ValueError('Ungültiger Suchmodus')
    if not isfinite(float(minimum_rrr)) or minimum_rrr <= 0 or horizon_15m <= 0:
        raise ValueError('Ungültige Gesamtbewertungsparameter')
    common={'mode':mode,'status':'NUR BEOBACHTEN','entry':None,'stop':None,'target':None,
            'rrr':None,'atr15':None,'source':'','reason':'','horizon_15m':horizon_15m,
            'minimum_rrr':minimum_rrr,'matches':0,'direction':mode}
    if mode=='Seitwärts':
        items=list((range_result or {}).get('ideas') or [])
        if not items:
            return {**common,'status':'KEINE RANGE-GELEGENHEIT',
                    'reason':(range_result or {}).get('reason') or 'Noch keine bestätigte handelbare Range.'}
        near=[i for i in items if i.distance_atr<=.75]
        if not near:
            return {**common,'status':'RANGE ERKANNT · RÄNDER BEOBACHTEN',
                    'reason':'Bestätigte Zone an beiden Range-Rändern, Kurs aber in der Mitte; kein Einstieg.',
                    'source':'15M/1H-Range-Zonen','matches':len(items)}
        idea=sorted(near,key=lambda i:(0 if 'RANDREAKTION' in i.state else 1,
                                       i.distance_atr))[0]
        return {**common,'status':'RANGE-RANDREAKTION · NUR BEOBACHTEN' if 'RANDREAKTION' in idea.state
                else 'RANGE-RAND BEOBACHTEN','direction':idea.direction,
                'entry':idea.entry,'stop':idea.stop,'target':idea.target,'rrr':idea.gross_rrr,
                'atr15':idea.atr15,'source':f'Range {idea.timeframe} · {idea.edge}',
                'reason':idea.reason+' Keine Trendfolge-Freigabe oder empirisch geprüfte Range-Strategie.',
                'matches':len(items)}
    zone_ideas=[x for x in (zones_result or {}).get('candidates',[]) if x.direction==mode]
    fib_ideas=[x for x in (fib_result or {}).get('ideas',[]) if x.direction==mode]
    if not zone_ideas and not fib_ideas:
        return {**common,'status':'KEIN PASSENDES SETUP',
                'reason':'Weder Zonen- noch Fib-Struktur-Scanner liefert einen gültigen Kandidaten in dieser Richtung. '
                         'Fehlende Bedingungen werden oben in den Einzelmodulen erläutert.'}
    # 61.8 and 78.6 are alternative points of one impulse, not independent votes.
    matching=[]
    for z in zone_ideas:
        for f in fib_ideas:
            a=f.atr15
            if not isfinite(a) or a<=0:
                continue
            # Require overlap of both the pre-existing confirmed zone and the
            # actual planned price, not just matching directional labels.
            zone_overlap=(z.zone_low-.25*a <= f.entry_reference <= z.zone_high+.25*a)
            price_overlap=abs(z.entry-f.entry_reference)<=.75*a
            if zone_overlap and price_overlap:
                matching.append((z,f,abs(z.entry-f.entry_reference)/a))
    h4=trend_context.get('higher_trend')
    h1=trend_context.get('middle_trend')
    direction_name='Aufwärtstrend' if mode=='Long' else 'Abwärtstrend'
    hourly_ok=(h1==direction_name)
    higher_ok=(h4==direction_name and hourly_ok)
    if matching:
        # Favor fresh confirmed reaction, then compatible geometry, not maximum CRV.
        matching.sort(key=lambda pair:(0 if (pair[0].state=='TECHNISCHE REAKTION' and
                            pair[1].state=='TECHNISCHER 5M-TRIGGER (KEINE ORDER)') else 1,
                            pair[2],-min(pair[1].gross_rrr,3.0)))
        z,f,_=matching[0]
        confirmed=(higher_ok and z.state=='TECHNISCHE REAKTION' and
                   f.state=='TECHNISCHER 5M-TRIGGER (KEINE ORDER)' and
                   z.rrr>=minimum_rrr and f.gross_rrr>=minimum_rrr)
        status='GEMEINSAMER TECHNISCHER KANDIDAT · KEINE ORDER' if confirmed else 'KONFLUENZ · NUR BEOBACHTEN'
        # Fib scenario has one coherent impulse/stop/target; never fuse its
        # Entry with a different scanner's stop/target to inflate CRV.
        reason=('S/R-Szenario und 15M-Fibonacci liegen im selben Preisbereich. '
                f'Fib {f.fib_ratio*100:.1f} %: {f.state}; Zonen-Scanner: {z.state}. '
                'Gezeigte Preise/CRV stammen zusammenhängend aus dem Fibonacci-Szenario. ')
        if not hourly_ok:
            reason+='1H-Trend nicht in Suchrichtung bestätigt: nur beobachten. '
        elif not higher_ok:
            reason+='4H/1H nicht gemeinsam bestätigt: nur beobachten. '
        if z.rrr < minimum_rrr or f.gross_rrr < minimum_rrr:
            reason+='Das eingestellte Mindest-Brutto-CRV wird nicht von beiden Szenarien erreicht. '
        if not confirmed:
            reason+='Nicht alle Bestätigungen für einen technischen Kandidaten erfüllt.'
        else:
            reason+='Beide technischen Reaktionsprüfungen erfüllt, jedoch keine Handelsfreigabe oder Erfolgsprognose.'
        return {**common,'status':status,'entry':f.entry_reference,'stop':f.stop_reference,
                'target':f.target_reference,'rrr':f.gross_rrr,'atr15':f.atr15,
                'source':f'Fib {f.fib_ratio*100:.1f} % + bestätigte {z.timeframe}-Zone',
                'reason':reason,'matches':len(matching)}
    # No price confluence: do not conjure a joint result even if both scanners
    # have candidates in the SAME nominal direction.
    # If there is no joint match, select the most actionable *independent*
    # observation rather than always preferring the first Fibonacci level.
    # A confirmed zone reaction should not be hidden behind an untouched Fib.
    def observation_rank(item, source):
        state=str(item.state)
        if source == 'fib':
            strength=(0 if state=='TECHNISCHER 5M-TRIGGER (KEINE ORDER)' else
                      2 if 'REAKTION BEOBACHTET' in state or state.startswith('REAKTION') else
                      4 if 'GETESTET' in state else 7)
            rrr=item.gross_rrr
        else:
            strength=(0 if state=='TECHNISCHE REAKTION' else
                      2 if state.startswith('REAKTION') else 6)
            rrr=item.rrr
        # Do not prefer a numerically attractive but sub-minimum-CRV idea.
        return (0 if rrr >= minimum_rrr else 1, strength,
                0 if source=='zone' else 1)
    alternatives=[(z,'zone') for z in zone_ideas]+[(f,'fib') for f in fib_ideas]
    item,source=min(alternatives,key=lambda pair:observation_rank(*pair))
    from_fib=(source=='fib')
    gross = item.gross_rrr if from_fib else item.rrr
    insufficient_crv = gross < minimum_rrr
    return {**common,'status':('MINDEST-CRV NICHT ERREICHT · NUR BEOBACHTEN'
                                if insufficient_crv else
                                'KEINE GEMEINSAME BESTÄTIGUNG · BEOBACHTEN'),
            'entry':item.entry_reference if from_fib else item.entry,
            'stop':item.stop_reference if from_fib else item.stop,
            'target':item.target_reference if from_fib else item.target,
            'rrr':gross,
            'atr15':item.atr15 if from_fib else None,
            'source':'Einzelmodul Fibonacci' if from_fib else 'Einzelmodul S/R',
            'reason':'Kein bestätigter räumlicher Abgleich von Zonen- und Fibonacci-Entry. '
                     'Die Preise sind nur Referenzen eines Einzelmoduls; kein gemeinsamer Einstieg. '
                     + ('Mindest-Brutto-CRV verfehlt. ' if insufficient_crv else '')
                     + ('1H-Trend widerspricht der Suchrichtung.' if not hourly_ok else '')}
