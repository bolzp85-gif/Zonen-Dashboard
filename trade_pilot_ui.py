"""Streamlit read-only Trade-Pilot and FRED panels for existing Zonenradar."""
from __future__ import annotations
import math
import streamlit as st
import pandas as pd

from macro_compass import snapshot, context_text, health_summary
from risk_regime import assess as assess_risk_regime, FACTORS
from trade_pilot_engine import Plan, evaluate
from atr_target_context import assess as assess_atr_target
from trade_trend_bridge import derive_trend_context, direction_conflict
from trade_signal_scanner import scan as scan_setups, projected_euro_result
from risk_score_history import describe as describe_risk_history


@st.cache_data(ttl=3600,show_spinner=False)
def _macro_cached(key: str):
    return snapshot(key)


def _fmt(n: float | None, digits: int = 2) -> str:
    return 'k. A.' if n is None or not math.isfinite(float(n)) else f'{n:,.{digits}f}'


def _suggestion(zones: pd.DataFrame, direction: str, ref: float, atr: float):
    """Only already confirmed active S/R zones. No synthetic target or stop."""
    if zones is None or zones.empty or atr <= 0:
        return None
    active=zones[(zones['Status']!='Gebrochen')].copy()
    if active.empty:
        return None
    # focus only same reference TF for transparent structure, no aggregating unconfirmed zones.
    try:
        supports=active[(active['Seite']=='Unterstützung') & (active['Obergrenze'] < ref)]
        resist=active[(active['Seite']=='Widerstand') & (active['Untergrenze'] > ref)]
        if supports.empty or resist.empty:
            return None
        below=supports.sort_values('Obergrenze', ascending=False).iloc[0]
        above=resist.sort_values('Untergrenze', ascending=True).iloc[0]
        if direction=='Long':
            stop=float(below.Untergrenze)-.15*atr
            target=float(above.Untergrenze)-.10*atr
        else:
            stop=float(above.Obergrenze)+.15*atr
            target=float(below.Obergrenze)+.10*atr
        if (direction=='Long' and stop < ref < target) or (direction=='Short' and target < ref < stop):
            return {'stop':stop,'target':target,'support':(float(below.Untergrenze),float(below.Obergrenze)),
                    'resistance':(float(above.Untergrenze),float(above.Obergrenze))}
    except (KeyError,TypeError,ValueError,AttributeError):
        return None
    return None


def _render_data_health(records: list[dict], *, headline: bool = True) -> None:
    """Last FRED request state, NOT a continuous online/offline monitor."""
    summary = health_summary(records)
    if headline:
        st.markdown('#### 📡 FRED-Verbindung und Datenqualität')
    a,b,c,d=st.columns(4)
    a.metric('Abruf erfolgreich',f"{summary['online']}/{summary['total']}")
    b.metric('Aktuelle Daten',f"{summary['current']}/{summary['total']}")
    c.metric('Veraltete Daten',summary['outdated'])
    d.metric('Fehlende / fehlerhafte Daten',summary['unavailable'])
    checked=summary['last_checked_utc'] or 'Kein Abruf protokolliert'
    st.caption('Letzte API-Anfrage (UTC): '+checked+'. Die Resultate sind bis zu 60 Minuten zwischengespeichert. '
               '„ONLINE“ bedeutet: Die LETZTE HTTP-Anfrage wurde beantwortet; kein kontinuierlicher Verfügbarkeitscheck. '
               'Eine online erreichte Reihe kann trotzdem veraltet, leer oder fachlich nicht auswertbar sein.')
    for sid in FACTORS:
        row=next((r for r in summary['rows'] if r['id']==sid),None)
        if row:
            signal=('🟢' if row['fetch_status']=='ONLINE' and row['data_status']=='AKTUELL'
                    else '🟠' if row['fetch_status']=='ONLINE' else '🔴')
            st.caption(f"{signal} **{sid}**: Abruf {row['fetch_status']} · Daten {row['data_status']} · "
                       f"Beobachtung {row['observation_date'] or '–'}")
    with st.expander('Alle neun FRED-Datenreihen: Online/Offline, Alter und Datenlücken',expanded=False):
        table=[]
        for row in summary['rows']:
            fetch=row['fetch_status']
            label=('🟢 ONLINE' if fetch=='ONLINE' else
                   '🔴 ABRUF FEHLGESCHLAGEN' if fetch=='ABRUF FEHLGESCHLAGEN' else
                   '⚪ KEIN API-KEY' if fetch=='KEIN API-KEY' else '⚪ NICHT GEPRÜFT')
            data=row['data_status']
            data_label=('🟢 AKTUELL' if data=='AKTUELL' else
                        '🟠 VERALTET' if data=='VERALTET' else
                        '🟠 UNVOLLSTÄNDIG' if data=='UNVOLLSTÄNDIG' else '🔴 FEHLT / FEHLER')
            table.append({'Indikator':row['name'],'FRED-ID':row['id'],
                          'API-Verbindung (letzter Abruf)':label,'Datenstand':data_label,
                          'Letzte Beobachtung':row['observation_date'] or '–',
                          'Alter (Tage)':row['age_days'] if row['age_days'] is not None else '–',
                          'Max. Alter (Tage)':row['max_age_days'],
                          'Letzter Abruf (UTC)':row['checked_at_utc'] or '–',
                          'Hinweis':row['reason']})
        st.dataframe(pd.DataFrame(table),hide_index=True,width='stretch')
        st.caption('FRED-Verbindung erfolgreich ≠ ausreichende Datenabdeckung für die Risk-Ampel. '
                   'Diese erfordert zusätzlich die Pflichtreihen und den jeweils aktuellen, auswertbaren Datenstand.')


def _render_auto_trend_context(context: dict) -> None:
    st.markdown('#### 📈 Automatisch aus dem Zonenradar übernommene Trendstruktur')
    st.caption('Keine zweite Berechnung und keine manuellen Trend-Selectboxen: exakt dieselben bestätigten '
               'Swings, EMA-Filter und letzten Kerzenschlüsse wie im Reiter „Trendradar“.')
    table=[]
    for tf in ('4H','1H','15Min','5Min','1D'):
        r=context['frames'][tf]
        table.append({'Zeitebene':tf,'Trend (automatisch)':r.trend,
                      'Swing-Struktur':r.structure,'EMA-Filter':r.ema_filter,
                      'Letzter Schluss UTC':r.candle_close_utc})
    st.dataframe(pd.DataFrame(table),hide_index=True,width='stretch')
    if context['feed_stale']:
        st.warning('eToro-Kerzendaten sind möglicherweise verzögert. Trendstruktur kann veraltet sein.')
    if not context['complete']:
        st.error('Trenddaten für 4H, 1H oder 15Min fehlen. Keine reguläre Trade-Planungsfreigabe.')
    if context['bias'] in ('Long','Short'):
        st.info(f"4H und 1H bestätigen dieselbe Richtung: **{context['bias']}**. "
                'Das ist eine automatische Trend-Einordnung, kein Einstiegs- oder Freigabesignal.')
    else:
        st.info('4H und 1H sind nicht beide in derselben bestätigten Richtung. '
                'Automatischer Trend-Bias: neutral/uneinheitlich. Kein erzwungenes Long-/Short-Signal.')


def render_risk_regime(secret_key: str, symbol: str):
    """Market regime is separate from the trade planning decision."""
    st.subheader('🚦 Marktumfeld · Risk-on / Risk-off')
    st.caption('Eigenständige US-Finanzstress-Ampel aus FRED · 45/40/15 (vorläufig, nicht empirisch validiert). Getrennt von Trendradar, Gear und Handelsentscheidung.')
    if st.button('🔄 FRED-Daten jetzt neu abrufen',key='tp_force_fred_refresh',
                 help='Löscht nur den FRED-Zwischenspeicher. Nächster Abruf kann einige Sekunden dauern.'):
        _macro_cached.clear()
        st.rerun()
    records = _macro_cached(secret_key)
    regime = assess_risk_regime(records)
    # A single compact status line avoids three very tall metric cards on iPhone.
    score_text = f"{regime['score']:.2f}/100" if regime['score'] is not None else '–'
    label = regime['label']
    display = f"{label} · Score {score_text} · Abdeckung {regime['coverage_pct']:.0f}%"
    if regime['color']=='green': st.success('🟢 '+display)
    elif regime['color']=='red': st.error('🔴 '+display)
    elif regime['color']=='yellow': st.warning('🟡 '+display)
    else: st.info('⚪ '+display)
    st.caption('Pflichtdaten: ' + ', '.join(f"{r['id']}: {r['status']}" for r in regime['details']))
    trend_history = describe_risk_history(records)
    st.markdown('#### 📈 Risk-Score-Verlauf · 5 / 10 / 20 Handelstage')
    if regime['reliable'] and trend_history['rows']:
        delta_text=[]
        for horizon in (5,10,20):
            change = trend_history['changes'].get(horizon)
            if change is None:
                delta_text.append(f"**{horizon} T:** –")
            else:
                delta_text.append(f"**{horizon} T:** {change['delta']:+.2f} Pkt.")
        st.markdown('  ·  '.join(delta_text))
        st.caption('Positiv = weniger Finanzstress; negativ = zunehmende Anspannung. '
                   'Verlauf aus den derzeit abrufbaren FRED-Zeitreihen, keine Echtzeit-Prognose.')
        with st.expander('Risk-Score als Verlaufsgrafik und methodische Einschränkungen', expanded=False):
            chart = pd.DataFrame(trend_history['rows']).set_index('date')
            chart.index = pd.to_datetime(chart.index, utc=True)
            st.line_chart(chart[['score']], height=190)
            st.caption(trend_history['note'])
    else:
        st.info(trend_history['reason'] or 'Für den Verlauf liegen nicht genügend gemeinsame historische Daten vor.')
    with st.expander('📡 FRED-Datenqualität · alle 9 Reihen', expanded=False):
        _render_data_health(records, headline=False)
    if not regime['reliable']:
        st.warning('Keine verlässliche Risk-Ampel: ' + '; '.join(regime['reasons']))
    elif regime['label'] == 'RISK-ON':
        st.caption('Weniger Stress in den betrachteten US-Risikoindikatoren; kein automatisch bullisches Signal für das ausgewählte Asset.')
    elif regime['label'] == 'RISK-OFF':
        st.caption('Erhöhter Stress in den betrachteten US-Risikoindikatoren; kein automatisches Short-Signal.')
    else:
        st.caption('Gemischtes US-Finanzmarktumfeld; aus der Ampel allein ist keine Richtung ableitbar.')
    st.caption('Risk-on/off bezeichnet eine übergreifende Risikostimmung und gilt nicht als Long-/Short-Aussage für Gold, Öl oder Devisen. Keine automatische Änderung der Positionsgröße.')
    st.markdown('#### 📊 Gewichtete Säulen · Punkteverteilung')
    st.caption('Jeder Teilscore: 0 = hoher Stress, 100 = geringer Stress. '
               'Gewichtete Punkte = Einzelbewertung × Gewicht / 100. '
               'Die Summe der Punkte ergibt bei vollständigen Daten den Risk-Score (maximal 100).')
    table = []
    for r in regime['details']:
        cfg = FACTORS[r['id']]
        raw = _fmt(r['value'], 3) if r['value'] is not None else '–'
        score_text = _fmt(r['score'], 2) if r['score'] is not None else '–'
        points_text = _fmt(r['points'], 2) if r['points'] is not None else '–'
        table.append({
            'Säule': r['name'],
            'Istwert': f"{raw} {r['unit']}" if r['value'] is not None else '–',
            'Einzelwertung / 100': score_text,
            'Gewicht': f"{r['weight_pct']:.0f} %",
            'Punkte (von Max.)': f"{points_text} / {r['max_points']:.0f}" if r['points'] is not None else f"– / {r['max_points']:.0f}",
            'Beobachtet am': r['date'] or '–',
            'Datenstatus': r['status'],
            'Letzter Abruf': r['fetch_status'],
        })
        with st.container():
            st.markdown(f"**{r['name']}** · Gewicht {r['weight_pct']:.0f} %")
            if r['points'] is None:
                st.warning(f"Keine Punkteberechnung – {r['reason']}. Status: {r['status']}. Letzte Beobachtung: {r['date'] or '–'}.")
            else:
                st.progress(min(1.0, max(0.0, r['score']/100.0)),
                            text=f"{r['score']:.2f} / 100 Einzelpunkte")
                st.write(f"**{r['points']:.2f} / {r['max_points']:.0f} gewichtete Punkte** "
                         f"= {r['score']:.2f} × {r['weight_pct']:.0f} %")
                st.caption(f"Istwert {raw} {r['unit']} · Beobachtung {r['date']} · "
                           f"Abruf {r['fetch_status']} · Daten {r['status']}")
    if regime['score'] is not None:
        st.metric('Gesamtpunkte aus den drei Säulen', f"{regime['score']:.2f} / 100")
    else:
        st.info(f"Vorhandene Säulen liefern zusammen {regime['scored_points']:.2f} Punkte. "
                'Das ist KEIN Gesamt-Risk-Score: Mindestens eine notwendige Säule fehlt.')
    with st.expander('Detailtabelle, Berechnungsgrenzen und Modellannahmen', expanded=False):
        st.dataframe(pd.DataFrame(table), hide_index=True, width='stretch')
        st.caption('Niedriger Stress ab / hoher Stress ab: ' + '; '.join(
            f"{r['name']}: {FACTORS[r['id']]['risk_on']} / {FACTORS[r['id']]['risk_off']}"
            for r in regime['details']))
        st.caption('Vorläufiges Modell 45 % High-Yield-Spread, 40 % VIX und 15 % NFCI. '
                   'Erstanträge (ICSA) bleiben im Makro-Kompass, ohne Score-Gewicht. '
                   'ALLE drei Säulen müssen aktuelle Daten liefern: keine stillschweigende Normalisierung '
                   'oder Umverteilung bei Ausfall. Die Schwellen 65/35 und die Faktorgrenzen sind '
                   'transparente Annahmen, nicht empirisch validiert.')
        st.caption('Quellen: https://fred.stlouisfed.org/series/BAMLH0A0HYM2 · '
                   'https://fred.stlouisfed.org/series/VIXCLS · '
                   'https://fred.stlouisfed.org/series/NFCI')
    st.caption('Beobachtungsdatum ≠ Veröffentlichungszeitpunkt. Revidierte, verspätete oder fehlende Daten können die Einstufung verändern. Kein Wirtschafts-News-Kalender.')
    st.divider()
    return regime


def render_macro(secret_key: str, symbol: str):
    st.subheader('🌐 Makro-Kompass · FRED')
    st.caption('Deskriptiver US-Makro-Kontext. Kein News-Überraschungsindikator, kein Trading-Signal. Zeitreihen werden ungefähr stündlich neu abgerufen.')
    if not secret_key:
        st.warning('FRED_API_KEY fehlt in Streamlit Secrets. Alle Reihen zeigen den Status KEIN API-KEY; die übrigen Funktionen laufen weiter.')
    with st.spinner('FRED-Daten laden …'):
        records = _macro_cached(secret_key)
    st.info(context_text(symbol))
    rows=[]
    for r in records:
        val=r.get('value')
        rows.append({'Kennzahl':r.get('name'),'FRED-ID':r['id'],
                     'Wert':_fmt(val,2) + (' ' + r.get('unit','') if val is not None else ''),
                     'Beobachtung':r.get('date') or '–','Tendenz':r.get('trend') or '–',
                     'Datenalter':r.get('status'), 'Verbindung':r.get('fetch_status','NICHT GEPRÜFT'),
                     'Zuletzt abgefragt (UTC)':r.get('checked_at_utc') or '–', 'Hinweis':r.get('remark','')})
    st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch')
    if any(r.get('status')!='AKTUELL' for r in records):
        st.warning('Mindestens eine FRED-Reihe ist veraltet oder nicht verfügbar. Fehlende Werte werden nicht neutral oder positiv bewertet.')
    st.caption('Beobachtungsdatum ≠ Veröffentlichungsdatum. CPI wird als Jahresänderung des Indexes berechnet. Daten können nachträglich revidiert werden. US-Daten beschreiben für FX nur eine Marktseite.')


def render_tradepilot(*, client, instrument_id: int | None, symbol: str,
                      frames: dict, zones: pd.DataFrame, trend: pd.DataFrame,
                      is_demo: bool, stale: bool, fred_key: str):
    render_risk_regime(fred_key, symbol)
    st.subheader('🛡️ Trade-Pilot 3.0 · CFD-Planung')
    st.caption('Reiner Lese- und Planungsmodus ohne Handelskosten. KEINE Orders, KEINE Kontodatenabfrage. Brutto-Risiko und Brutto-CRV berücksichtigen weder Spread noch Gebühren, Overnight-Finanzierung oder Slippage.')
    render_macro(fred_key,symbol)
    st.divider()
    trend_context = derive_trend_context(trend, feed_stale=stale)
    _render_auto_trend_context(trend_context)
    st.divider()
    st.markdown('### 🎯 Automatische technische Einstiegskandidaten')
    st.caption('Ohne Kontostand, Margin oder manuelle Trendangabe. Nur bestätigte S/R-Zonen, '
               '4H-/1H-Trend, abgeschlossene 15Min-Kerzen, ATR und Mindest-Brutto-CRV. '
               'Die Auswahl ist eine Regelheuristik, keine statistisch geprüfte Rangfolge nach Trefferwahrscheinlichkeit.')
    pick1, pick2 = st.columns(2)
    min_auto_rrr = pick1.number_input('Mindest-Brutto-CRV',min_value=.5,max_value=10.0,
                                     value=1.8,step=.1,key='tp_auto_min_rrr')
    auto_horizon = pick2.selectbox('Zielhorizont (Kerzen der jeweiligen Zonen-Zeitebene)',
                                   [2,4,8,12,24,48],index=2,key='tp_auto_horizon')
    search_result = scan_setups(frames=frames,zones=zones,trend_context=trend_context,
                                 minimum_rrr=float(min_auto_rrr),horizon_bars=int(auto_horizon),
                                 is_demo=is_demo,stale=stale)
    ideas = search_result['candidates']
    if not ideas:
        st.warning('Kein passender technischer Einstiegskandidat: ' +
                   (search_result['reason'] or 'Die Regeln sind derzeit nicht erfüllt.'))
    else:
        shown = ideas[:5]
        options=list(range(len(shown)))
        chosen = st.selectbox('Beste derzeit passende technische Szenarien',options,
                   format_func=lambda i:(f"{i+1}. {shown[i].direction} · {shown[i].timeframe} · "
                     f"{shown[i].state} · CRV {shown[i].rrr:.2f}"),key='tp_auto_pick')
        best=shown[chosen]
        if best.state=='TECHNISCHE REAKTION':
            st.success('Technische Reaktion auf abgeschlossener 15Min-Kerze bestätigt. '
                       'Kein automatischer Broker-Einstieg; News, Spread und Ausführung bleiben ungeprüft.')
        elif best.state=='REAKTION MIT WARNHINWEIS':
            st.warning('Reaktion sichtbar, aber die ATR-/Strukturprüfung enthält Warnhinweise. Kein freigegebenes Einstiegssignal.')
        else:
            st.info('Beobachtungsszenario: Warten, bis der Kurs die Zone erreicht und eine abgeschlossene Kerze reagiert. '
                    'Der ausgewiesene Entry ist ein theoretischer Grenzkurs, kein ausführbarer eToro-Preis.')
        st.caption(best.reason)
        st.markdown(f"**{best.direction} · {best.timeframe}** · bestätigte Zone {best.zone_low:,.5f}–{best.zone_high:,.5f} "
                    f"· Qualität {best.quality:.0f}/100 · 15Min-Datenstand: {best.signal_time_utc}")
        st.markdown(f"**Entry:** {best.entry:,.5f}  |  **Stop:** {best.stop:,.5f}  |  **Target:** {best.target:,.5f}")
        st.markdown(f"**Brutto-CRV:** {best.rrr:.2f} · **Stop/ATR:** {best.stop_atr:.2f}× · "
                    f"**Ziel/ATR:** {best.target_atr:.2f}×")
        st.caption('Positionen ohne bestätigte 4H-/1H-Trendübereinstimmung werden bewusst nicht erzeugt. '
                   'Eine günstige Verhältniszahl ist keine garantierte Trefferquote.')
        with st.expander('💶 Optional: Brutto-Gewinn/Verlust für deinen Einsatz berechnen',expanded=False):
            st.caption('Der Betrag ist **keine Pflichtangabe** und beeinflusst die Kandidatensuche nicht. '
                       'Zur Rechnung brauchen wir einen nominalen EUR-Positionswert. Eingesetzte Margin allein '
                       'reicht ohne den tatsächlichen Hebel nicht.')
            money_mode=st.radio('Was möchtest du eingeben?',
                 ['Nominaler Positionswert (€)','Margin-Einsatz (€) und Broker-Hebel'],
                  key='tp_auto_money_mode')
            amount=st.number_input('Betrag (€)',min_value=0.0,value=0.0,step=100.0,
                                   key='tp_auto_amount')
            lev=1.0
            if money_mode.startswith('Margin'):
                lev=st.number_input('Tatsächlich gewählter Broker-Hebel',min_value=1.0,max_value=30.0,
                                    value=2.0,step=1.0,key='tp_auto_leverage')
            if amount>0:
                nominal = float(amount)*float(lev)
                projection=projected_euro_result(entry=best.entry,stop=best.stop,target=best.target,
                                                  direction=best.direction,nominal_eur=nominal)
                st.write(f"**Angenommener Nominalwert:** {nominal:,.2f} €")
                st.write(f"**Brutto-Gewinn am Target:** +{projection['gross_profit_eur']:,.2f} €")
                st.write(f"**Brutto-Verlust am Stop:** −{projection['gross_loss_eur']:,.2f} €")
                st.warning('Reine lineare Preisänderungsrechnung für einen angenommenen EUR-Nominalwert, '
                           'keine brokerverifizierte CFD-Stückzahl oder Ausführungsprognose. '
                           'Währungsumrechnung, Kontraktmultiplikator, Spread, Gebühren, Finanzierung, '
                           'Slippage und Kurslücken sind nicht enthalten; tatsächlicher Verlust kann höher sein.')
            else:
                st.caption('Ohne Betrag werden nur Entry, Stop, Target, ATR und Brutto-CRV angezeigt.')
        with st.expander('Weitere Kandidaten und Filterdiagnose',expanded=False):
            st.dataframe(pd.DataFrame([{'Status':c.state,'Richtung':c.direction,'Zone-TF':c.timeframe,
                      'Entry':c.entry,'Stop':c.stop,'Target':c.target,'Brutto-CRV':round(c.rrr,2),
                      'Stop/ATR':round(c.stop_atr,2),'Ziel/ATR':round(c.target_atr,2),
                      'Zonenqualität':c.quality} for c in ideas]),hide_index=True,width='stretch')
            st.caption(f"Geprüfte Zonenpaare: {search_result['evaluated']}; unter Mindest-CRV: {search_result['rejected_crv']}.")
    st.divider()
    show_manual=st.checkbox('Erweiterte manuelle CFD- und Risikoplanung öffnen (optional)',value=False,
                            key='tp_show_manual_advanced')
    if not show_manual:
        st.caption('Die manuelle Kontostand-/Margin-Planung ist bewusst ausgeblendet. '
                   'Sie wird für die automatische technische Suche nicht benötigt.')
        return
    st.markdown('### Erweiterte manuelle Handelsplanung')
    current=float(frames['5Min'].close.iloc[-1])
    c1,c2,c3=st.columns(3)
    with c1:
        direction=st.radio('Geplante Handelsrichtung (nicht die Trendstruktur)', ['Long','Short'],horizontal=True,key='tp_direction',
                           help='Die Trendstruktur wird oben automatisch übernommen. Hier wählst du lediglich, welchen Trade du prüfen möchtest.')
        risk_pct=st.number_input('Risiko pro Trade (%)',min_value=0.0,max_value=2.0,value=1.0,step=.25,key='tp_risk')
        daily_pct=st.number_input('Tagesrisikolimit (%)',min_value=0.0,max_value=5.0,value=2.0,step=.5,key='tp_daily')
    with c2:
        equity=st.number_input('Planungskonto / Equity (€)',min_value=0.0,value=10000.0,step=1000.0,key='tp_equity')
        free_margin=st.number_input('Tatsächlich frei verfügbare Margin (€)',min_value=0.0,value=0.0,step=100.0,
                                    help='Nicht einfach Equity minus gebundene Margin schätzen. Beim Broker prüfen.',key='tp_freemargin')
        realized=st.number_input('Realisierte Tagesverluste (€)',min_value=0.0,value=0.0,key='tp_realized')
    with c3:
        open_risk=st.number_input('Offenes Risiko anderer Positionen (€)',min_value=0.0,value=0.0,key='tp_openrisk')
        min_crv=st.number_input('Mindest-Brutto-CRV',min_value=.5,max_value=5.,value=1.5,step=.1,key='tp_minrr')
        stress=st.checkbox('Hoher Stress / Müdigkeit / Zeitdruck',value=False,key='tp_stress')

    st.markdown('**Marktpreis und technische Planung**')
    st.caption('Referenzpreis aus abgeschlossenen eToro-OHLC-Kerzen. Keine Ausführungskurse; Handelskosten werden bewusst nicht berechnet.')
    selected_tf=st.selectbox('Struktur-Zeitebene für Vorschlagszonen und ATR',['1H','4H','15Min'],index=0,key='tp_srtf')
    horizon_bars=st.selectbox('Geplanter Zeithorizont bis zum Kursziel (Kerzen der ausgewählten Zeitebene)',
                              [2,4,8,12,24,48],index=1,key='tp_target_horizon',
                              help='Keine Prognose: dient ausschließlich zum Vergleich der nötigen Netto-Zielstrecke mit der aktuellen ATR pro Kerze.')
    selected=zones[zones.Zeitebene==selected_tf] if zones is not None and not zones.empty else pd.DataFrame()
    atr_tf=float(__import__('engine').atr(frames[selected_tf]).iloc[-1])
    idea=_suggestion(selected,direction,current,atr_tf)
    if idea:
        st.info(f"Technischer Zonen-Vorschlag ({selected_tf}) · Support {idea['support'][0]:.5f}–{idea['support'][1]:.5f} · Widerstand {idea['resistance'][0]:.5f}–{idea['resistance'][1]:.5f}. Bitte manuell plausibilisieren.")
    else:
        st.warning('Für die gewählte Zeitebene fehlen passende bestätigte Support- und Widerstandsbereiche auf beiden Seiten des Kurses. Kein automatischer Stop/Target-Vorschlag.')
    p1,p2,p3=st.columns(3)
    with p1:
        entry=st.number_input('Referenz-Entry (Chartpreis)',min_value=0.0000001,value=current,format='%.5f',key=f'tp_entry_{instrument_id or 0}')
    with p2:
        fallback_stop=current-(atr_tf or 1.) if direction=='Long' else current+(atr_tf or 1.)
        stop=st.number_input('Referenz-Stop (Chartpreis)',min_value=0.0000001,value=max(.0000001,float(idea['stop'] if idea else fallback_stop)),format='%.5f',key=f'tp_stop_{instrument_id or 0}_{selected_tf}_{direction}')
    with p3:
        fallback_target=current+2*(atr_tf or 1.) if direction=='Long' else current-2*(atr_tf or 1.)
        target=st.number_input('Referenz-Ziel (Chartpreis)',min_value=0.0000001,value=max(.0000001,float(idea['target'] if idea else fallback_target)),format='%.5f',key=f'tp_target_{instrument_id or 0}_{selected_tf}_{direction}')
    if not idea:
        st.caption('⚠️ Vorbelegte Stop-/Zielwerte ohne beidseitige bestätigte Zonen sind reine Eingabebeispiele, keine technische Empfehlung.')
    st.caption('Alle drei Werte sind theoretische Chart-Referenzpreise; Spread, Gebühren, Finanzierung und Slippage sind ausdrücklich NICHT eingerechnet.')
    product_key=f'tp_{instrument_id or 0}'
    with st.expander('CFD-Kontraktdaten und Margin (nur für Positionsgröße erforderlich)',expanded=False):
        s1,s2=st.columns(2)
        with s1:
            point_native=st.number_input('Geldwert je 1,0 Preispunkt / CFD-Einheit (native Währung)',min_value=0.0000001,value=1.0,format='%.6f',key=f'tp_point_{product_key}')
            eur_native=st.number_input('EUR je 1 Einheit nativer Währung',min_value=0.0000001,value=1.0,format='%.6f',key=f'tp_fx_{product_key}')
            confirm_spec=st.checkbox('CFD-Punktwert, Währung, Einheitenschritte und Hebel beim Broker geprüft',key=f'tp_spec_confirm_{product_key}')
        with s2:
            lev=st.number_input('Broker-Hebel (Planungs-Margin)',min_value=1.0,max_value=30.0,value=2.0,step=1.0,key=f'tp_leverage_{product_key}')
            min_units=st.number_input('Mindest-CFD-Einheiten',min_value=.000001,value=.01,format='%.6f',key=f'tp_min_units_{product_key}')
            unit_step=st.number_input('Einheitsschritt',min_value=.000001,value=.01,format='%.6f',key=f'tp_unitstep_{product_key}')
        st.caption('Ohne bestätigte Kontraktdaten wird das Brutto-CRV angezeigt, aber keine Positionsgröße errechnet. Die vorbefüllten 1,0-Werte sind keine bestätigten eToro-Produkteigenschaften.')
    news=st.checkbox('Veröffentlichungskalender geprüft: keine unmittelbar bevorstehenden relevanten High-Impact-News (<30 Min)',value=False,key='tp_news')
    higher_val = trend_context['higher_trend']
    tf_conflict = direction_conflict(trend_context, direction)
    if tf_conflict:
        st.warning(f'4H-Trend widerspricht {direction}: {higher_val}.')
    elif higher_val not in ('Aufwärtstrend','Abwärtstrend'):
        st.info(f'4H-Trend: {higher_val}. Kein gerichteter Trend bestätigt.')
    plan=Plan(direction=direction,entry=float(entry),stop=float(stop),target=float(target),atr=atr_tf,
              equity_eur=float(equity),free_margin_eur=float(free_margin),base_risk_pct=float(risk_pct),
              daily_limit_pct=float(daily_pct),realized_loss_eur=float(realized),other_open_risk_eur=float(open_risk),
              value_per_point_native=float(point_native),eur_per_native=float(eur_native),leverage=float(lev),
              min_units=float(min_units),unit_step=float(unit_step),
              minimum_rrr=float(min_crv),broker_specs_confirmed=bool(confirm_spec),
              news_checked=bool(news),market_data_current=(not stale and trend_context['complete']),
              is_demo=is_demo,high_stress=bool(stress),higher_tf_conflict=bool(tf_conflict))
    result=evaluate(plan)
    # Pure rule-based context tier. It does NOT change allowed risk or claim edge.
    middle_trend = trend_context['middle_trend']
    entry_trend = trend_context['entry_trend']
    atr_context=assess_atr_target(
        direction=direction,entry=float(entry),stop=float(stop),target=float(target),
        atr=atr_tf,horizon_bars=int(horizon_bars),zones=selected,
        higher_trend=higher_val,
        middle_trend=middle_trend)
    dirword='Aufwärtstrend' if direction=='Long' else 'Abwärtstrend'
    h4_ok=higher_val==dirword
    h1_ok=middle_trend==dirword
    m15_ok=entry_trend==dirword
    if result['status']!='PLANUNG REGELKONFORM':
        gear=1
    elif h4_ok and h1_ok and m15_ok and (result.get('gross_rrr') or 0)>=2.5:
        gear=5
    elif h4_ok and h1_ok and m15_ok:
        gear=4
    elif h4_ok and h1_ok:
        gear=3
    else:
        gear=2
    if atr_context['status'] != 'KEIN UNMITTELBARER WARNHINWEIS':
        gear=min(gear,3)  # Nur erläuternde Qualitätsstufe, keine Risikoerhöhung.
    st.markdown('### 📋 Entscheidungskarte')
    st.caption(f'Gear {gear}/5 · regelbasierte Beschreibung der MTF-Ausrichtung, keine Trefferwahrscheinlichkeit und keine zusätzliche Risikoerhöhung.')
    if result['status']=='PLANUNG REGELKONFORM' and atr_context['status']=='KEIN UNMITTELBARER WARNHINWEIS':
        st.success('🟢 Basisregeln erfüllt; keine unmittelbaren ATR-/Zonenwarnungen – KEINE Broker-Orderfreigabe')
    elif result['status']=='PLANUNG REGELKONFORM':
        st.warning('🟠 Basis-Risikolimits eingehalten; technische ATR-/Zielprüfung offen – KEINE Broker-Orderfreigabe')
    else:
        st.error('🔴 Keine Planungsfreigabe · '+str(len(result['blockers']))+' Blocker')
    x1,x2,x3=st.columns(3)
    x1.metric('Brutto-CRV · ohne Kosten',_fmt(result.get('gross_rrr')))
    x2.metric('CFD-Einheiten',_fmt(result.get('units'),4) if confirm_spec else '–')
    x3.metric('Stop-Risiko · ohne Kosten',_fmt(result.get('stop_risk_eur'))+' €' if confirm_spec else '–')
    st.markdown('#### 📐 ATR-basierte Ziel- und Stop-Plausibilität')
    st.caption('Kein verändertes CRV und keine geschätzte Trefferquote: ATR(14) misst die vergangene Schwankungsbreite der gewählten Zeitebene. Der Zielhorizont ist eine Planungsannahme.')
    t1,t2,t3=st.columns(3)
    t1.metric('Stop / ATR',_fmt(atr_context.get('stop_atr'))+' ×' if atr_context.get('stop_atr') is not None else 'k. A.')
    t2.metric('Target / ATR',_fmt(atr_context.get('target_atr'))+' ×' if atr_context.get('target_atr') is not None else 'k. A.')
    t3.metric('Netto-Zielstrecke je Kerze / ATR',_fmt(atr_context.get('required_atr_per_bar'))+' ×' if atr_context.get('required_atr_per_bar') is not None else 'k. A.')
    st.caption(f'Geplanter Horizont: {horizon_bars} × {selected_tf}. Die pro Kerze benötigte Netto-Kursbewegung ist nur eine arithmetische Vergleichsgröße, keine Bewegungsprognose.')
    if atr_context['status']=='KEIN UNMITTELBARER WARNHINWEIS':
        st.info('ATR-/Zonenkontext: Keine unmittelbaren Warnhinweise aus den verfügbaren Daten. Zielerreichung bleibt ungewiss.')
    else:
        st.warning(f"ATR-/Zonenkontext: {atr_context['status']} (heuristische Einordnung, keine statistische Wahrscheinlichkeit).")
    for message in atr_context['warnings']:
        st.write('⚠️ '+message)
    if atr_context['obstacles']:
        with st.expander(f"Vorgelagerte Gegen-Zonen ({len(atr_context['obstacles'])})",expanded=True):
            st.dataframe(pd.DataFrame([
                {'Zeitebene':z['zeitebene'] or selected_tf,
                 'Untergrenze':z['untergrenze'],'Obergrenze':z['obergrenze'],
                 'Distanz vom Einstieg':z['abstand']}
                for z in atr_context['obstacles'][:15]]),hide_index=True,width='stretch')
    elif selected is None or selected.empty:
        st.caption('Keine bestätigten Zonen für diese Zeitebene verfügbar: Fehlende Hindernisse sind hier keine Entwarnung.')
    st.caption('Die Ziel-Plausibilität ist ein Hinweis, kein statistisch validierter Filter. Weder CRV noch Risikobudget werden durch die ATR-Distanz künstlich verändert.')
    for message in result.get('blockers',[]): st.write('⛔ '+message)
    for message in result.get('warnings',[]): st.write('⚠️ '+message)
    if result.get('gross_rrr') is not None:
        breakdown={
            'Brutto-CRV (ohne Kosten)':_fmt(result['gross_rrr']),
            'Stop-Abstand (Preispunkte)':_fmt(result['stop_points'],5),
            'Ziel-Abstand (Preispunkte)':_fmt(result['reward_points'],5),
            'Stop-Abstand / ATR':_fmt(result.get('stop_atr')),
            'Ziel-Abstand / ATR':_fmt(result.get('target_atr')),
            'Netto-Zielstrecke je Kerze / ATR':_fmt(atr_context.get('required_atr_per_bar')),
            'Geplanter Zielhorizont (Kerzen)':str(horizon_bars),
        }
        if result.get('risk_budget_eur') is not None:
            breakdown.update({
                'Risikobudget (€; ohne Kosten)':_fmt(result['risk_budget_eur']),
                'Verbleibendes Tagesbudget (€)':_fmt(result['daily_remaining_eur']),
                'Max. Einheiten Risiko':_fmt(result['max_risk_units'],4),
                'Max. Einheiten Margin':_fmt(result['max_margin_units'],4),
                'Limitierender Faktor':result['limit_reason'],
                'Stop-Risiko (€; ohne Kosten)':_fmt(result['stop_risk_eur']),
                'Brutto-Chance (€; ohne Kosten)':_fmt(result['gross_reward_eur']),
                'Planungs-Margin (€)':_fmt(result['margin_eur']),
                'Nominalwert (€)':_fmt(result['nominal_eur']),
            })
        with st.expander('Brutto-Risikorechnung und Limits im Detail',expanded=True):
            st.dataframe(pd.DataFrame(breakdown.items(),columns=['Kennzahl','Wert']),hide_index=True,width='stretch')
    st.warning('Handelskosten bewusst ausgeklammert: Spread, Kommissionen, Overnight-Gebühren und Slippage erhöhen reale Verluste bzw. verringern Gewinne. Angezeigtes Brutto-CRV ist daher kein Netto-CRV. Stops können bei Gaps schlechter ausgeführt werden; auch Margin und Wechselkurse können sich ändern.')
    st.caption('Keine Anlageberatung und keine automatische Orderausführung.')
