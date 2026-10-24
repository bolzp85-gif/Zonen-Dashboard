"""v0.7.2 OHLC/SR sideways-only watcher: no trend reversal or trade approval.

A range requires a 1H or 15Min 'Seitwärts' trend classification AND an
active, confirmed support/resistance pair bracketing the last closed price.
Both range edges are WATCH scenarios, never 2 independent live signals.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
import pandas as pd
from engine import atr
from atr_target_context import assess as assess_atr_target
from trade_signal_scanner import _confirmed_reaction


@dataclass(frozen=True)
class RangeIdea:
    direction: str
    edge: str
    timeframe: str
    entry: float
    stop: float
    target: float
    gross_rrr: float
    atr15: float
    stop_atr: float
    target_atr: float
    range_low: float
    range_high: float
    state: str
    reason: str
    target_context: str
    distance_atr: float
    horizon_bars: int


def scan_ranges(*, frames: dict, zones: pd.DataFrame | None, trend_context: dict,
                minimum_rrr: float = 1.8, horizon_bars: int = 8,
                is_demo: bool = False, stale: bool = False) -> dict:
    if not isinstance(horizon_bars, int) or not 1 <= horizon_bars <= 120:
        raise ValueError('Ungültiger Zeithorizont')
    if not isfinite(float(minimum_rrr)) or not .5 <= minimum_rrr <= 10:
        raise ValueError('Ungültiges Mindest-CRV')
    result={'ideas':[], 'reason':'', 'range_confirmed':False,
            'checked':0,'rejected_crv':0}
    if is_demo or stale or not trend_context.get('complete') or not trend_context.get('current'):
        result['reason']='Demo-/veraltete/unvollständige Daten: keine aktuellen Range-Szenarien.'
        return result
    d=frames.get('15Min')
    if not isinstance(d,pd.DataFrame) or len(d)<35 or not {'open','high','low','close','time'}.issubset(d.columns):
        result['reason']='Mindestens 35 vollständige 15M-Kerzen erforderlich.'
        return result
    try:
        price=float(d.close.iloc[-1]); a=float(atr(d).iloc[-1])
    except (ValueError,TypeError,IndexError,KeyError):
        result['reason']='Preis/ATR fehlen.'
        return result
    if not all(isfinite(x) and x>0 for x in (price,a)):
        result['reason']='Preis/ATR ungültig.'
        return result
    frame_states=trend_context.get('frames') or {}
    state15=getattr(frame_states.get('15Min'),'trend',None)
    state1=trend_context.get('middle_trend')
    if state15!='Seitwärts' and state1!='Seitwärts':
        result['reason']='Weder 1H noch 15M bestätigen eine Seitwärtsstruktur. Keine Range erzwingen.'
        return result
    if not isinstance(zones,pd.DataFrame) or not {'Zeitebene','Status','Seite','Untergrenze','Obergrenze'}.issubset(zones.columns):
        result['reason']='Bestätigte S/R-Zonen fehlen.'
        return result
    # Same-TF pairs prevent assembling a synthetic range from incompatible levels.
    for tf in ('15Min','1H'):
        active=zones.loc[(zones.Zeitebene==tf)&(zones.Status.isin(('Aktiv','Bestätigt','Getestet')))]
        supports=active.loc[active.Seite=='Unterstützung']
        resistances=active.loc[active.Seite=='Widerstand']
        pairs=[]
        for s in supports.itertuples(index=False):
            for r in resistances.itertuples(index=False):
                try:
                    sl,sh,rl,rh=map(float,(s.Untergrenze,s.Obergrenze,r.Untergrenze,r.Obergrenze))
                except (TypeError,ValueError):
                    continue
                if not all(isfinite(x) and x>0 for x in (sl,sh,rl,rh)):
                    continue
                if not sl<sh<rl<rh or not sh<=price<=rl:
                    continue
                width=rl-sh
                if width < max(2*a,.0025*price):
                    continue
                # Avoid extremely remote, broad ranges masquerading as intraday setups.
                if width > 12*a:
                    continue
                pairs.append((width,sl,sh,rl,rh))
        if not pairs:
            continue
        _,sl,sh,rl,rh=min(pairs,key=lambda p:p[0])
        result['range_confirmed']=True
        for direction in ('Long','Short'):
            if direction=='Long':
                entry=sh+.05*a; stop=sl-.15*a; target=rl-.10*a
                lo,hi=sl,sh; distance=max(0.0,price-sh)/a
                edge='Unterkante / Unterstützung'
            else:
                entry=rl-.05*a; stop=rh+.15*a; target=sh+.10*a
                lo,hi=rl,rh; distance=max(0.0,rl-price)/a
                edge='Oberkante / Widerstand'
            valid=(0<stop<entry<target) if direction=='Long' else (0<target<entry<stop)
            if not valid:
                continue
            rrr=abs(target-entry)/abs(entry-stop)
            result['checked']+=1
            if rrr < minimum_rrr:
                result['rejected_crv']+=1
                continue
            reaction=_confirmed_reaction(d,direction=direction,zone_low=lo,zone_high=hi,atr=a)
            target_check=assess_atr_target(direction=direction,entry=entry,stop=stop,
                   target=target,atr=a,horizon_bars=horizon_bars,zones=active,
                   higher_trend=trend_context.get('higher_trend','Übergang / unklar'),
                   middle_trend=state1 or 'Übergang / unklar')
            if reaction and distance<=.75:
                state='RANGE-RANDREAKTION · NUR BEOBACHTEN'
                reason='Letzte geschlossene 15M-Kerze reagiert am Range-Rand. Range-Regeln nicht historisch validiert.'
            elif distance <=.75:
                state='RANGE-RAND NAH · REAKTION ABWARTEN'
                reason='Kurs liegt nahe am Range-Rand; eine geschlossene Reaktionskerze fehlt.'
            else:
                state='RANGE-RAND ENTFERNT · BEOBACHTEN'
                reason='Kurs liegt nicht am jeweiligen Rand; kein kurzfristiger Einstieg.'
            if target_check['status']!='KEIN UNMITTELBARER WARNHINWEIS':
                reason+=' ATR-/Zielwarnung: '+target_check['status']+'.'
            result['ideas'].append(RangeIdea(direction,edge,tf,entry,stop,target,rrr,a,
                  abs(entry-stop)/a,abs(target-entry)/a,sh,rl,state,reason,
                  target_check['status'],distance,horizon_bars))
        break
    if not result['range_confirmed']:
        result['reason']='Keine ausreichend breite, aktuelle Range zwischen bestätigten S/R-Zonen erkannt.'
    elif not result['ideas']:
        result['reason']='Range erkannt, aber keine beidseitig sinnvolle CRV-/Stop-/Zielgeometrie.'
    result['ideas'].sort(key=lambda i:(0 if 'RANDREAKTION' in i.state else 1,
                                       i.distance_atr,-min(i.gross_rrr,3)))
    return result
