"""Conservative candidate finder for pre-confirmed eToro S/R levels, no broker orders.

Ranks *technical scenarios*; only a confirmed closed 15m reaction merits the
technical trigger label. No account balance, sizing, probabilities or fees here.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from typing import Any
import pandas as pd
from atr_target_context import assess as assess_atr_target

@dataclass(frozen=True)
class Candidate:
    direction: str
    timeframe: str
    entry: float
    stop: float
    target: float
    rrr: float
    atr: float
    stop_atr: float
    target_atr: float
    quality: float
    zone_low: float
    zone_high: float
    price_distance_atr: float
    state: str
    reason: str
    target_context: str
    horizon_bars: int
    reference_close: float
    signal_time_utc: str
    scenario: str = ""
    confirmation_needed: str = ""


@dataclass(frozen=True)
class Scenario:
    direction: str
    label: str
    style: str
    trigger_eligible: bool
    interpretation: str
    confirmation_needed: str


def derive_scenarios(trend_context: dict) -> list[Scenario]:
    """Classify observable contexts WITHOUT converting an unclear higher TF into a signal.

    A confirmed 4H+1H directional alignment is required before a candle can
    become a TECHNISCHE REAKTION. All other modes are watch-only; even an
    observed 15m bounce may not override the unresolved higher-timeframe trend.
    """
    if not trend_context.get('complete'):
        return []
    h4 = trend_context.get('higher_trend', 'Übergang / unklar')
    h1 = trend_context.get('middle_trend', 'Übergang / unklar')
    up, down = 'Aufwärtstrend', 'Abwärtstrend'
    side, unclear = 'Seitwärts', 'Übergang / unklar'
    scenarios: list[Scenario] = []

    def add(direction: str, label: str, style: str, eligible: bool, note: str, need: str):
        scenarios.append(Scenario(direction, label, style, eligible, note, need))

    if h4 == h1 == up:
        add('Long','Trendfolge Long','TREND',True,'4H und 1H bestätigen den Aufwärtstrend.',
            '15M-Aufwärtsbestätigung + abgeschlossene bullische Reaktionskerze an Unterstützung; 5M darf nicht abwärts bestätigt sein.')
    elif h4 == h1 == down:
        add('Short','Trendfolge Short','TREND',True,'4H und 1H bestätigen den Abwärtstrend.',
            '15M-Abwärtsbestätigung + abgeschlossene bärische Reaktionskerze an Widerstand; 5M darf nicht aufwärts bestätigt sein.')
    elif h4 == up:
        add('Long','4H-Aufwärtstrend · 1H-Korrektur/Übergang','KORREKTUR',False,
            f'4H ist aufwärts, 1H derzeit {h1}. Potenzielle Rücksetzer nur beobachten.',
            '1H wieder als Aufwärtstrend bestätigt; danach 15M-Aufwärtsbestätigung und bullische Reaktionskerze.')
    elif h4 == down:
        add('Short','4H-Abwärtstrend · 1H-Erholung/Übergang','KORREKTUR',False,
            f'4H ist abwärts, 1H derzeit {h1}. Potenzielle Erholungen nur beobachten.',
            '1H wieder als Abwärtstrend bestätigt; danach 15M-Abwärtsbestätigung und bärische Reaktionskerze.')
    elif h4 == side and h1 in (side, unclear):
        for direction, kind in (('Long','Unterstützung'),('Short','Widerstand')):
            add(direction,f'Seitwärtsmarkt · {kind} beobachten','RANGE',False,
                '4H ist seitwärts; keine bestätigte Trendfolge. Nur Grenzen der vorhandenen S/R-Zonen betrachten.',
                f'Bestätigte Reaktion an {kind}, anschließend Bruch/Bestätigung auf 1H oder klare Range-Regeln; bis dahin kein Einstiegssignal.')
    elif h4 == side and h1 in (up, down):
        direction = 'Long' if h1 == up else 'Short'
        add(direction, 'Range mit 1H-' + ('Aufwärts-' if direction=='Long' else 'Abwärts-') + 'Impuls', 'RANGE',False,
            '4H seitwärts, 1H gerichtet: mögliche Bewegung innerhalb der Range, keine übergeordnete Trendbestätigung.',
            '4H-Ausbruch mit bestätigter Struktur oder separat validierte Range-Einstiegsregeln; 15M-Reaktion alleine reicht nicht.')
    elif h4 == unclear and h1 in (up, down):
        direction = 'Long' if h1==up else 'Short'
        add(direction,'1H-' + ('Aufwärts-' if direction=='Long' else 'Abwärts-') + 'trend · 4H offen','ÜBERGANG',False,
            'Der 1H-Trend ist bestätigt, 4H aber unklar. Kein gleichgerichtetes höheres Trendsignal.',
            '4H-Trend bestätigt dieselbe Richtung; 15M- und 5M-Konflikte lösen sich und Reaktionskerze wird bestätigt.')
    else:
        for direction, kind in (('Long','Unterstützung'),('Short','Widerstand')):
            add(direction, f'Unklarer Markt · {kind} beobachten','ÜBERGANG',False,
                '4H und 1H liefern keine gemeinsame, bestätigte Richtung; beide Seiten bleiben reine Beobachtung.',
                f'Klare übergeordnete Struktur oder später validiertes Range-Setup; bestätigte Reaktion an {kind} allein genügt nicht.')
    return scenarios


def _float(x: Any) -> float | None:
    try:
        a = float(x)
        return a if isfinite(a) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _active_zones(zones: pd.DataFrame | None, timeframe: str) -> pd.DataFrame:
    required = {'Zeitebene', 'Status', 'Seite', 'Untergrenze', 'Obergrenze'}
    if not isinstance(zones, pd.DataFrame) or not required.issubset(zones.columns):
        return pd.DataFrame()
    part = zones.loc[(zones.Zeitebene == timeframe) & (zones.Status != 'Gebrochen')].copy()
    return part


def _confirmed_reaction(frame: pd.DataFrame, *, direction: str,
                        zone_low: float, zone_high: float, atr: float) -> bool:
    """The newest *completed* 15m candle must touch/reclaim a S/R zone.

    This uses neither future candles nor inferred intrabar order flow.
    """
    if not isinstance(frame, pd.DataFrame) or len(frame) < 2:
        return False
    last = frame.iloc[-1]
    c, o, lo, hi = [_float(last[k]) for k in ('close', 'open', 'low', 'high')]
    if any(v is None for v in (c, o, lo, hi)):
        return False
    if direction == 'Long':
        return (lo <= zone_high + .10 * atr and hi >= zone_low and
                c >= zone_high + .05 * atr and c > o)
    return (hi >= zone_low - .10 * atr and lo <= zone_high and
            c <= zone_low - .05 * atr and c < o)


def scan(*, frames: dict, zones: pd.DataFrame | None, trend_context: dict,
         minimum_rrr: float = 1.5, horizon_bars: int = 8,
         is_demo: bool = False, stale: bool = False,
         quality_floor: float = 0.0, direction_filter: str | None = None,
         horizon_unit: str = 'source') -> dict:
    """Find trend-aligned S/R pullback scenarios and separately label a closed-candle trigger.

    Only considers 1H and 15Min context zones; never fabricates targets.
    Trend-misaligned and ranging setups remain WATCH-only regardless of candle
    reaction until an independent, rule-based trigger framework is validated.
    """
    if not isfinite(float(minimum_rrr)) or not .5 <= minimum_rrr <= 10:
        raise ValueError('Mindest-CRV außerhalb 0,5–10')
    if not isinstance(horizon_bars, int) or not 1 <= horizon_bars <= 96:
        raise ValueError('Horizont muss 1–96 Kerzen betragen')
    if direction_filter not in (None,'Long','Short'):
        raise ValueError('Richtungsfilter ungültig')
    if horizon_unit not in ('source','15Min'):
        raise ValueError('Horizont-Einheit ungültig')
    scenarios = derive_scenarios(trend_context)
    if direction_filter:
        scenarios = [s for s in scenarios if s.direction == direction_filter]
        if not scenarios and trend_context.get('complete'):
            # User-requested countertrend search is allowed for observation,
            # never to override a confirmed higher-timeframe direction.
            scenarios = [Scenario(direction_filter,
                f'Richtungssuche {direction_filter} · höherer Trend dagegen/unklar',
                'ÜBERGANG',False,
                'Entgegen der derzeit klassifizierten Marktphase: Gegenrichtung nur beobachten.',
                'Mindestens 1H-Trendwechsel in Suchrichtung bestätigen; 4H-Ausrichtung, '
                '15M-Reaktion und 5M-Filter für einen regulären technischen Trigger erforderlich.')]

    base = {'candidates': [], 'scenarios': scenarios, 'evaluated': 0,
            'rejected_crv': 0, 'reason': None,
            'market_data_current': not (stale or is_demo or not trend_context.get('current'))}
    if not trend_context.get('complete') or not scenarios:
        return {**base, 'reason':'Trendstruktur unvollständig – keine automatische Einstiegsbewertung.'}
    h4, h1 = trend_context.get('higher_trend'), trend_context.get('middle_trend')
    expected_bias = ('Long' if h4 == h1 == 'Aufwärtstrend' else
                     'Short' if h4 == h1 == 'Abwärtstrend' else
                     'Neutral / uneinheitlich')
    if trend_context.get('bias') != expected_bias:
        return {**base, 'reason':'Widerspruch zwischen Trend-Bias und 4H-/1H-Struktur: Datenkonsistenz prüfen.'}
    if is_demo or stale or not trend_context.get('current'):
        return {**base, 'reason':'Demodaten oder verzögerte Kurse – keine aktuellen technischen Kandidaten.'}
    if '15Min' not in frames or '5Min' not in frames:
        return {**base, 'reason':'15Min-/5Min-Kerzen fehlen.'}
    tf15 = trend_context['frames']['15Min'].trend
    tf5 = trend_context['frames']['5Min'].trend
    # Every confirmation uses the ATR of the observed 15M candle, even
    # when the underlying support/resistance zone originates on 1H.
    candle_atr15 = _float(__import__('engine').atr(frames['15Min']).iloc[-1])
    if candle_atr15 is None or candle_atr15 <= 0:
        return {**base, 'reason':'ATR(14) der 15Min-Bestätigungskerze fehlt.'}
    close = _float(frames['15Min'].close.iloc[-1])
    if close is None or close <= 0:
        return {**base, 'reason':'Kein gültiger abgeschlossener 15-Minuten-Schlusskurs.'}
    results = []
    for scenario in scenarios:
        direction = scenario.direction
        expected = 'Aufwärtstrend' if direction == 'Long' else 'Abwärtstrend'
        for timeframe in ('1H', '15Min'):
            if timeframe not in frames:
                continue
            raw_atr = _float(__import__('engine').atr(frames[timeframe]).iloc[-1])
            if raw_atr is None or raw_atr <= 0:
                continue
            active = _active_zones(zones, timeframe)
            if active.empty:
                continue
            support_side = 'Unterstützung' if direction == 'Long' else 'Widerstand'
            obstacle_side = 'Widerstand' if direction == 'Long' else 'Unterstützung'
            source = active.loc[active.Seite == support_side]
            obstacles = active.loc[active.Seite == obstacle_side]
            if source.empty or obstacles.empty:
                continue
            for _, zone in source.iterrows():
                zl, zh = _float(zone.Untergrenze), _float(zone.Obergrenze)
                if zl is None or zh is None or zl >= zh:
                    continue
                quality = _float(zone.get('Qualitaet_Punkte',0)) or 0.0
                if quality < quality_floor:
                    continue
                if direction == 'Long':
                    if zl > close + .15 * raw_atr:
                        continue
                    planned_entry = zh + .05 * raw_atr
                    stop = zl - .15 * raw_atr
                    target_list = sorted((_float(r.Untergrenze) for r in obstacles.itertuples(index=False)
                                         if _float(r.Untergrenze) is not None), key=float)
                    target_edges = [v for v in target_list if v > max(planned_entry, close) + .15 * raw_atr]
                    if not target_edges:
                        continue
                    target = target_edges[0] - .10 * raw_atr
                    dist = max(0.0, close - zh) / raw_atr
                else:
                    if zh < close - .15 * raw_atr:
                        continue
                    planned_entry = zl - .05 * raw_atr
                    stop = zh + .15 * raw_atr
                    target_list = sorted((_float(r.Obergrenze) for r in obstacles.itertuples(index=False)
                                         if _float(r.Obergrenze) is not None), reverse=True)
                    target_edges = [v for v in target_list if v < min(planned_entry, close) - .15 * raw_atr]
                    if not target_edges:
                        continue
                    target = target_edges[0] + .10 * raw_atr
                    dist = max(0.0, zl - close) / raw_atr
                if dist > 3.0:
                    continue  # far-off levels are not useful as near-term candidates
                # A single 15m candle cannot override an unresolved 4H / 1H bias.
                reaction_visible = _confirmed_reaction(frames['15Min'], direction=direction,
                                zone_low=zl, zone_high=zh, atr=candle_atr15)
                confirmed = (scenario.trigger_eligible and reaction_visible and
                             tf15 == expected and
                             tf5 != ('Abwärtstrend' if direction == 'Long' else 'Aufwärtstrend'))
                entry = close if confirmed else planned_entry
                geometry = (0 < stop < entry < target) if direction == 'Long' else (0 < target < entry < stop)
                if not geometry:
                    continue
                rrr = abs(target-entry) / abs(entry-stop)
                base['evaluated'] += 1
                if rrr + 1e-10 < minimum_rrr:
                    base['rejected_crv'] += 1
                    continue
                effective_horizon = ((horizon_bars + 3)//4 if timeframe=='1H' and horizon_unit=='15Min'
                                     else horizon_bars)
                context = assess_atr_target(direction=direction, entry=entry, stop=stop, target=target,
                    atr=raw_atr, horizon_bars=effective_horizon, zones=active,
                    higher_trend=trend_context['higher_trend'], middle_trend=trend_context['middle_trend'])
                # A 'confirmed' technical trigger must also clear meaningful ATR and obstacle warnings.
                state = ('TECHNISCHE REAKTION' if confirmed and context['status']=='KEIN UNMITTELBARER WARNHINWEIS'
                         else 'REAKTION MIT WARNHINWEIS' if confirmed
                         else 'REAKTION BEOBACHTEN · TREND OFFEN' if reaction_visible and not scenario.trigger_eligible
                         else 'KURZFRISTIGE KORREKTUR BEOBACHTEN' if scenario.style == 'TREND' and
                               (tf15 == ('Abwärtstrend' if direction == 'Long' else 'Aufwärtstrend') or
                                tf5 == ('Abwärtstrend' if direction == 'Long' else 'Aufwärtstrend'))
                         else 'KORREKTUR BEOBACHTEN' if scenario.style == 'KORREKTUR'
                         else 'RANGE BEOBACHTEN' if scenario.style == 'RANGE'
                         else 'ÜBERGANG BEOBACHTEN' if scenario.style == 'ÜBERGANG'
                         else 'ZONE BEOBACHTEN')
                reasons = ('Geschlossene 15Min-Kerze reagiert an Zone; beide höheren Trends und 15Min bestätigt.' if confirmed
                           else '15Min-Kerze reagiert an Zone; 4H-/1H-Struktur noch nicht einheitlich. KEINE Einstiegsfreigabe.' if reaction_visible and not scenario.trigger_eligible
                       else '15Min-Kerze reagiert an Zone, aber 15Min-/5Min-Trendfilter noch nicht passend. KEINE Einstiegsfreigabe.' if reaction_visible
                           else 'Theoretisches Szenario an bestätigter Zone; Reaktion noch nicht bestätigt.')
                reasons += ' ' + scenario.interpretation
                reasons += f' Kurzfristig: 15Min={tf15}, 5Min={tf5}.'
                if context['status'] != 'KEIN UNMITTELBARER WARNHINWEIS':
                    reasons += f" ATR-/Strukturprüfung: {context['status']}."
                ts = str(frames['15Min'].time.iloc[-1] + pd.Timedelta(minutes=15))
                results.append(Candidate(direction=direction, timeframe=timeframe, entry=entry, stop=stop,
                                         target=target, rrr=rrr, atr=raw_atr,
                                         stop_atr=abs(entry-stop)/raw_atr,
                                         target_atr=abs(target-entry)/raw_atr, quality=quality,
                                         zone_low=zl, zone_high=zh, price_distance_atr=dist,
                                         state=state, reason=reasons, target_context=context['status'],
                                         horizon_bars=effective_horizon, reference_close=close, signal_time_utc=ts,
                                         scenario=scenario.label, confirmation_needed=scenario.confirmation_needed))
    # Do NOT sort by highest RRR alone, which tends to favor distant/unreachable targets.
    rank = {'TECHNISCHE REAKTION':0, 'REAKTION MIT WARNHINWEIS':1,
            'REAKTION BEOBACHTEN · TREND OFFEN':2, 'ZONE BEOBACHTEN':3,
            'KURZFRISTIGE KORREKTUR BEOBACHTEN':4,
            'KORREKTUR BEOBACHTEN':4, 'RANGE BEOBACHTEN':5,
            'ÜBERGANG BEOBACHTEN':6}
    results.sort(key=lambda r:(rank[r.state], round(r.price_distance_atr,3), -r.quality,
                               -min(r.rrr,3.0), 0 if r.timeframe=='1H' else 1))
    # In range/transition contexts show both sides if valid, rather than filling
    # every visible slot with a single direction's many almost-identical zones.
    if len({s.direction for s in scenarios}) == 2:
        picked = []
        for side in ('Long', 'Short'):
            first = next((c for c in results if c.direction == side), None)
            if first is not None:
                picked.append(first)
        picked.extend(c for c in results if c not in picked)
        results = picked
    base['candidates'] = results[:8]
    if not results:
        base['reason'] = ('Für die beobachteten Marktphasen fehlt derzeit ein geeignetes bestätigtes '
                          'S/R-Zonenpaar mit dem eingestellten Mindest-Brutto-CRV. '
                          'Die Regime-Beobachtungen stehen trotzdem unten zur Verfügung.')
    return base


def projected_euro_result(*, entry: float, stop: float, target: float,
                          direction: str, nominal_eur: float) -> dict:
    """Illustrative gross returns for a *declared notional EUR exposure*, not margin.

    Linear price tracking is assumed; no broker-specific contract sizing,
    FX conversion, spreads, financing, gap risk or execution costs included.
    """
    if direction not in ('Long','Short') or not all(
        _float(x) is not None for x in (entry, stop, target, nominal_eur)
    ) or entry <= 0 or nominal_eur < 0:
        raise ValueError('Ungültige Planungseingaben')
    valid = (0 < stop < entry < target) if direction == 'Long' else (0 < target < entry < stop)
    if not valid:
        raise ValueError('Ungültige Preisstruktur')
    sign = 1 if direction == 'Long' else -1
    return {'gross_loss_eur': nominal_eur * abs(entry - stop) / entry,
            'gross_profit_eur': nominal_eur * sign * (target - entry) / entry,
            'notional_eur': nominal_eur}
