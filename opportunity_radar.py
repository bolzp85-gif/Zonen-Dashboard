"""Three-stage read-only opportunity radar for confirmed S/R, BOS/CHoCH and ranges.

Stage 1 denotes a *location/event*, NEVER an executable plan. Stage 2 is a
planning/watch scenario. Stage 3 inherits the existing strict technical gate.
No filtering criterion or lack of price data is silently interpreted as a trade.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from typing import Any
import pandas as pd
from engine import atr


@dataclass(frozen=True)
class Opportunity:
    stage: int
    direction: str
    source: str
    label: str
    timeframe: str
    detail: str
    blockers: str
    price: float | None = None
    gross_rrr: float | None = None


def _num(value: Any) -> float | None:
    try:
        n = float(value)
        return n if isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _observation(item, *, source: str, stage: int, label: str, blockers: str,
                 price_attr: str, rrr_attr: str, timeframe: str) -> Opportunity:
    return Opportunity(stage=stage,direction=item.direction,source=source,label=label,
                       timeframe=timeframe,detail=str(item.state),blockers=blockers,
                       price=_num(getattr(item,price_attr,None)),
                       gross_rrr=_num(getattr(item,rrr_attr,None)))


def collect(*, mode: str, frames: dict, zones: pd.DataFrame | None, trend_context: dict,
            zones_result: dict | None, fib_result: dict | None,
            range_result: dict | None, final_eval: dict | None,
            minimum_rrr: float, is_demo: bool, stale: bool) -> dict:
    """Cross-module opportunity *visibility* without relaxing trigger gates.

    Fib 61.8 and 78.6 are alternative levels of one impulse; stage counts group
    them into one observation. Raw zones lack stop/target and MUST NOT get CRV.
    """
    if mode not in ('Long','Short','Seitwärts'):
        raise ValueError('Ungültiger Suchmodus')
    if not isfinite(float(minimum_rrr)) or minimum_rrr <= 0:
        raise ValueError('Ungültiges CRV')
    result={'stages':{1:[],2:[],3:[]}, 'counts':{1:0,2:0,3:0},
            'diagnostics':{}, 'reason':''}
    if is_demo or stale or not trend_context.get('complete') or not trend_context.get('current'):
        result['reason']='Demo, veraltete oder unvollständige Trend-/Kerzendaten: keine aktuellen Chancen.'
        return result
    d15=frames.get('15Min')
    if not isinstance(d15,pd.DataFrame) or d15.empty or 'close' not in d15:
        result['reason']='15M-Referenzkurs fehlt.'
        return result
    current=_num(d15.close.iloc[-1])
    if current is None or current<=0:
        result['reason']='Letzter 15M-Schlusskurs ungültig.'
        return result
    a_by_tf={}
    for tf in ('15Min','1H'):
        d=frames.get(tf)
        if isinstance(d,pd.DataFrame) and len(d)>=20:
            try:
                a=_num(atr(d).iloc[-1])
                if a is not None and a>0:
                    a_by_tf[tf]=a
            except (KeyError, ValueError, IndexError):
                pass
    zs=zones_result or {}
    fs=fib_result or {}
    rs=range_result or {}
    fe=final_eval or {}
    result['diagnostics']={
        'zonenpaare_geprueft':int(zs.get('evaluated',0)),
        'zonen_crv_ausgeschlossen':int(zs.get('rejected_crv',0)),
        'fib_impulspaare_geprueft':int(fs.get('swing_diagnostics',{}).get('pairs_checked',0)),
        'fib_impulspaare_akzeptiert':int(fs.get('swing_diagnostics',{}).get('accepted',0)),
        'range_geprueft':int(rs.get('checked',0)),
        'range_crv_ausgeschlossen':int(rs.get('rejected_crv',0)),
    }

    # Stage 3 must inherit the existing confluence and higher-trend gates.
    expected='Aufwärtstrend' if mode=='Long' else 'Abwärtstrend'
    higher_aligned=(mode!='Seitwärts' and trend_context.get('higher_trend')==expected and
                    trend_context.get('middle_trend')==expected)
    joint=(higher_aligned and fe.get('status','').startswith('GEMEINSAMER TECHNISCHER KANDIDAT'))
    if mode!='Seitwärts' and joint and fe.get('direction',mode)==mode:
        price=_num(fe.get('entry'))
        rrr=_num(fe.get('rrr'))
        if price is not None and rrr is not None and rrr>=minimum_rrr:
            result['stages'][3].append(Opportunity(3,mode,'Zonen + Fibonacci',
                'Gemeinsam bestätigter technischer Kandidat','15Min',fe.get('reason',''),
                'Brokerkurs, News, Gebühren und Ausführung nicht geprüft; keine Order.',price,rrr))
    if mode!='Seitwärts' and higher_aligned and not result['stages'][3]:
        # The already-tested S/R-only technical trigger is a legitimate single
        # module candidate, but never represented as combined confirmation.
        for z in zs.get('candidates',[]) or []:
            if z.direction==mode and z.state=='TECHNISCHE REAKTION' and z.rrr>=minimum_rrr:
                result['stages'][3].append(_observation(z,source='S/R-Einzelmodul',stage=3,
                   label='Bestätigter technischer S/R-Kandidat (ohne Fib-Konfluenz)',
                   blockers='Fibonacci-Zusammenfall fehlt; keine Order/Erfolgsprognose.',
                   price_attr='entry',rrr_attr='rrr',timeframe=z.timeframe))

    if mode=='Seitwärts':
        range_ideas=list(rs.get('ideas',[]) or [])
        for x in range_ideas:
            result['stages'][2].append(_observation(x,source='Range-S/R',stage=2,
                label=f'Range-Rand beobachten · {x.edge}',
                blockers='Nur WATCH: Range-Regeln nicht als Entry-Strategie validiert.',
                price_attr='entry',rrr_attr='gross_rrr',timeframe=x.timeframe))
    else:
        for z in zs.get('candidates',[]) or []:
            if z.direction!=mode or z.state=='TECHNISCHE REAKTION':
                continue
            result['stages'][2].append(_observation(z,source='Bestätigte S/R-Zone',stage=2,
                label=f'{mode} an bestätigter {z.timeframe}-Zone beobachten',
                blockers=z.confirmation_needed or 'Weitere Trend-/Reaktionsbestätigung erforderlich.',
                price_attr='entry',rrr_attr='rrr',timeframe=z.timeframe))
        # Group 61.8 and 78.6 alternatives under one *structure*, not two chances.
        fib_groups={}
        for x in fs.get('ideas',[]) or []:
            if x.direction!=mode or 'ÜBERLAUFEN' in x.state:
                continue
            key=(x.direction,x.broken_at_utc,round(x.impulse_start,8),round(x.impulse_end,8))
            fib_groups.setdefault(key,[]).append(x)
        for group in fib_groups.values():
            # Prefer tested/rejected over an untouched fib; keep alternative levels
            # in same description for transparent counting.
            group.sort(key=lambda x:(0 if 'TRIGGER' in x.state else
                                     1 if ('REAKTION' in x.state or 'GETESTET' in x.state) else 2,
                                     -x.gross_rrr))
            x=group[0]
            levels=', '.join(f'{y.fib_ratio*100:.1f} %' for y in sorted(group,key=lambda y:y.fib_ratio))
            is_single_trigger=x.state=='TECHNISCHER 5M-TRIGGER (KEINE ORDER)'
            if is_single_trigger and higher_aligned and not joint and x.gross_rrr>=minimum_rrr:
                # Same strict rule in existing Fib scanner, but independently confirmed.
                result['stages'][3].append(_observation(x,source='Fibonacci-Einzelmodul',stage=3,
                    label=f'Technischer Fibonacci-Kandidat · {x.fib_ratio*100:.1f} %',
                    blockers='Ohne gemeinsamen Zonen-Scanner-Entry; keine Broker-/Orderfreigabe.',
                    price_attr='entry_reference',rrr_attr='gross_rrr',timeframe='15Min'))
            else:
                result['stages'][2].append(_observation(x,source='15M BOS/CHoCH + Fibonacci',stage=2,
                    label=f'{mode}-Strukturbruch · Fib-Alternativen {levels}',
                    blockers=x.confirmation or 'Rücklauf/5M-Reaktion oder höhere Trendbestätigung ausstehend.',
                    price_attr='entry_reference',rrr_attr='gross_rrr',timeframe='15Min'))
        for event in fs.get('early_breaks',[]) or []:
            if event.get('direction')==mode:
                result['stages'][1].append(Opportunity(1,mode,'15M-Strukturbruch',
                   f"Früher {event.get('kind','Strukturbruch')} – Impulsende fehlt",'15Min',
                   f"Bruch bei {event.get('level')} · {event.get('time_utc')}",
                   event.get('next') or 'Abgeschlossenes bestätigtes Impulsende abwarten.',
                   _num(event.get('level')),None))

    # Stage 1: from existing confirmed active zones, whether or not a target,
    # CRV or the second/opposite zone exists. No synthetic stop or target.
    cols={'Zeitebene','Status','Seite','Untergrenze','Obergrenze'}
    if isinstance(zones,pd.DataFrame) and cols.issubset(zones.columns):
        possible=[]
        for _,z in zones.iterrows():
            tf=str(z['Zeitebene']); a=a_by_tf.get(tf)
            if not a or z['Status'] not in ('Aktiv','Bestätigt','Getestet'):
                continue
            low,high=_num(z['Untergrenze']),_num(z['Obergrenze'])
            if low is None or high is None or not 0<low<=high:
                continue
            direction='Long' if z['Seite']=='Unterstützung' else 'Short' if z['Seite']=='Widerstand' else None
            if direction is None or (mode!='Seitwärts' and direction!=mode):
                continue
            distance=max(low-current,current-high,0)/a
            if distance>3.0:
                continue
            possible.append((distance,tf,direction,low,high))
        possible.sort(key=lambda v:v[0])
        for distance,tf,direction,low,high in possible[:6]:
            # Skip duplicated stage-2/3 price zones in same direction/timeframe.
            already=False
            for stage in (2,3):
                for item in result['stages'][stage]:
                    if (item.direction==direction and item.timeframe==tf and
                        item.price is not None and low-.25*a_by_tf[tf] <= item.price <= high+.25*a_by_tf[tf]):
                        already=True
            if already:
                continue
            label=(f'{direction}-Kurszone in Reichweite' if mode!='Seitwärts' else
                   f'Range-Rand prüfen: {"Unterstützung" if direction=="Long" else "Widerstand"}')
            note=(f'Bestätigte {tf}-Zone {low:.5f}–{high:.5f}; '+
                  f'Abstand zum letzten 15M-Schlusskurs {distance:.2f}× ATR({tf}).')
            result['stages'][1].append(Opportunity(1,direction,'Bestätigte S/R-Zone',
                label,tf,note, 'Kein vollständiges Zonenpaar, kein nachgewiesener Rücklauf/Entry; '
                'Stop, Ziel und CRV hier absichtlich nicht berechnet.',(low+high)/2,None))

    # Don't inflate stage counts with duplicate price opportunities.
    for stage in (1,2,3):
        result['stages'][stage]=result['stages'][stage][:10]
        result['counts'][stage]=len(result['stages'][stage])
    if not any(result['counts'].values()):
        result['reason']='Keine passende aktuelle Gelegenheit in der gewählten Richtung. '
        result['reason']+='Es werden keine künstlichen Signale erzeugt.'
    return result
