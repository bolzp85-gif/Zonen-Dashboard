"""ATR-/Marktstruktur-Plausibilitätsanzeige, KEIN statistischer Prognosealgorithmus.

OHLC-ATR beschreibt Vergangenheit und ist keine erwartete künftige
Bewegungsrichtung. Die hier gewählten Warnschwellen sind transparente
Orientierungsheuristiken, nicht historisch kalibrierte Erfolgsgrenzen.
"""
from __future__ import annotations

import math
from typing import Any


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def opposing_zones(zones, *, direction: str, entry: float, target: float) -> list[dict]:
    """Finde aktive, bestätigte Gegen-Zonen, die den Weg zum Ziel schneiden.

    `zones` ist die im Zonenradar schon erzeugte Zonentabelle *einer* TF.
    Die Funktion verwendet nur explizit vorhandene bestätigte Bereiche;
    gebrochene Bereiche und unvollständige Datensätze bleiben außen vor.
    """
    if direction not in ("Long", "Short") or not all(_finite(x) for x in (entry, target)):
        return []
    if direction == 'Long' and target <= entry or direction == 'Short' and target >= entry:
        return []
    if zones is None or getattr(zones, 'empty', True):
        return []
    cols={'Status','Seite','Untergrenze','Obergrenze'}
    if not cols.issubset(zones.columns):
        return []
    kind = 'Widerstand' if direction == 'Long' else 'Unterstützung'
    low,high=sorted((float(entry),float(target)))
    found=[]
    for r in zones.itertuples(index=False):
        if getattr(r, 'Status', None) == 'Gebrochen' or getattr(r, 'Seite', None) != kind:
            continue
        a,b=getattr(r,'Untergrenze',None),getattr(r,'Obergrenze',None)
        if not _finite(a) or not _finite(b):
            continue
        a,b=float(a),float(b)
        if a >= b or b <= low or a >= high:
            continue
        # Erste Berührung von Entry aus; bereits am Einstieg überlappende
        # Widerstände/Unterstützungen haben Wegdistanz 0.
        contact=max(low,a) if direction == 'Long' else min(high,b)
        distance=abs(contact-entry)
        found.append({'untergrenze':a,'obergrenze':b,
                      'abstand':distance,'zeitebene':getattr(r,'Zeitebene','')})
    return sorted(found, key=lambda z:(z['abstand'],z['untergrenze']))


def assess(*, direction: str, entry: float, stop: float, target: float,
           atr: float, horizon_bars: int, zones=None,
           higher_trend: str | None=None, middle_trend: str | None=None) -> dict:
    """Deskriptives Kontextresultat, unabhängig von CFD-Größe/Kosten.

    'ATR je Kerze' = erforderliche NETTO-Richtungsstrecke / N / aktuelle ATR.
    Es ist keine stochastische Erfolgswahrscheinlichkeit, weder linear noch
    unabhängig von künftigem Trend und Kursweg zu interpretieren.
    """
    out={'status':'NICHT BEWERTBAR','warnings':[], 'blockers':[],
         'obstacles':[], 'target_atr':None, 'stop_atr':None,
         'required_atr_per_bar':None, 'atr_horizon_bars':None}
    prices=(entry,stop,target)
    if direction not in ('Long','Short') or not all(_finite(v) and float(v)>0 for v in prices):
        out['warnings'].append('Ungültige Referenzpreise oder Handelsrichtung.')
        return out
    valid = (stop < entry < target) if direction=='Long' else (target < entry < stop)
    if not valid:
        out['warnings'].append('Preisstruktur ungültig; ATR-Zielprüfung nicht möglich.')
        return out
    if isinstance(horizon_bars,bool) or not isinstance(horizon_bars,int) or not (1 <= horizon_bars <= 120):
        out['warnings'].append('Ungültiger Planungshorizont (1–120 abgeschlossene Kerzen).')
        return out
    if not _finite(atr) or atr <= 0:
        out['warnings'].append('ATR fehlt oder ist ungültig – keine Zielplausibilitätswertung.')
        return out
    stop_atr=abs(entry-stop)/atr
    target_atr=abs(target-entry)/atr
    directional_atr_per_bar=target_atr/horizon_bars
    out.update(target_atr=target_atr,stop_atr=stop_atr,
               required_atr_per_bar=directional_atr_per_bar,
               atr_horizon_bars=horizon_bars)
    out['obstacles']=opposing_zones(zones,direction=direction,entry=entry,target=target)
    if out['obstacles']:
        nearest=out['obstacles'][0]
        label='Widerstand' if direction=='Long' else 'Unterstützung'
        out['warnings'].append(
            f"{len(out['obstacles'])} aktive {label}-Zone(n) vor oder am Kursziel; "
            f"erste bei {nearest['untergrenze']:.5f}–{nearest['obergrenze']:.5f}.")
    if directional_atr_per_bar > 1.0:
        out['warnings'].append('Die nötige Netto-Zielbewegung pro geplanter Kerze übersteigt 1 ATR (Orientierungsheuristik).')
    elif directional_atr_per_bar > .5:
        out['warnings'].append('Das Ziel erfordert eine ausgeprägte gerichtete Bewegung im gewählten Zeithorizont.')
    elif target_atr >= 4.0:
        out['warnings'].append('Weit entferntes Ziel: mehrere ATR Gesamtstrecke; Trendstabilität prüfen.')
    preferred='Aufwärtstrend' if direction=='Long' else 'Abwärtstrend'
    counter='Abwärtstrend' if direction=='Long' else 'Aufwärtstrend'
    if higher_trend == counter or middle_trend == counter:
        out['warnings'].append('Mindestens ein berücksichtigter höherer Trend widerspricht der Handelsrichtung.')
    elif higher_trend not in (preferred, None) or middle_trend not in (preferred, None):
        out['warnings'].append('Übergeordneter Trend nicht auf beiden Zeitebenen eindeutig bestätigt.')
    if out['obstacles']:
        out['status']='HINDERNIS VOR ZIEL'
    elif directional_atr_per_bar > 1.0:
        out['status']='SEHR ANSPRUCHSVOLL'
    elif out['warnings']:
        out['status']='ZUSATZPRÜFUNG'
    else:
        out['status']='KEIN UNMITTELBARER WARNHINWEIS'
    return out
