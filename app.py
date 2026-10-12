import hmac
import time
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from engine import TF, analyze, demo_data, atr
from etoro import Client, FeedError
from zone_view import candle_table, bundle_zones, nearest_balanced, price_text, candle_fingerprint, zone_export_view

from zone_context import EMA_PERIODS, indicator_series, indicator_summary, add_zone_context

st.set_page_config(page_title='Zonenradar · eToro',page_icon='◈',layout='wide')
st.markdown('''<style>
.stApp {background: #0b1120;} [data-testid="stMetric"] {background:#142033;padding:16px;border-radius:12px;}
h1 {letter-spacing:-1px;} .block-container {padding-top:2rem;}
</style>''',unsafe_allow_html=True)
st.title('Zonenradar')
st.caption('eToro · 4H / 1H / 15Min / 5Min · Ansicht 0.3.0')

def secret(name):
    try:
        return str(st.secrets.get(name,''))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return ''

with st.sidebar:
    st.header('Arbeitsbereich')
    mode = st.radio('Datenquelle',['Demo · synthetische Daten','eToro · API'])
    count = st.select_slider('Kerzen je Zeitebene',options=[200,400,600,1000],value=400)
    stoch_length = st.selectbox('Stochastik-Lookback', [5,8,14], index=1, help='Glättung: 3 für K und 3 für D. Standard 8/3/3 wie im bereitgestellten Chart.')
    near_atr = st.slider('EMA-Nähe zur Zone in ATR', 0.0, 1.0, 0.25, 0.05, help='0 = nur innerhalb der Zone. Frei gewählte Anzeigeschwelle, nicht statistisch kalibriert. ATR und EMA stammen aus der Zonen-Zeitebene.')
    refresh = st.checkbox('Alle 60 Sekunden aktualisieren',value=False)
    st.caption('Aktualisierung nur bei aktiver Sitzung. Keine Hintergrund- oder Push-Alarme.')

client = None
instrument_id = None
label = 'Synthetischer Beispielmarkt'
if mode.startswith('eToro'):
    password = secret('APP_PASSWORD')
    if len(password) < 16:
        st.info('Einrichtung: APP_PASSWORD (mindestens 16 Zeichen) in Streamlit Secrets hinterlegen. Siehe START_HIER.md.')
        st.stop()
    if not st.session_state.get('unlocked'):
        with st.form('login'):
            entered = st.text_input('Dashboard-Passwort',type='password')
            login = st.form_submit_button('Entsperren')
        if login:
            if time.time() < st.session_state.get('next_login',0):
                st.warning('Bitte kurz warten.')
            elif hmac.compare_digest(entered.encode(),password.encode()):
                st.session_state.unlocked = True
                st.rerun()
            else:
                st.session_state.next_login = time.time()+5
                st.error('Passwort stimmt nicht.')
        st.stop()
    if st.sidebar.button('Sperren'):
        st.session_state.clear()
        st.rerun()
    api_key, user_key = secret('ETORO_API_KEY'), secret('ETORO_USER_KEY')
    if not api_key or not user_key:
        st.info('ETORO_API_KEY und ETORO_USER_KEY in Streamlit Secrets eintragen.')
        st.stop()
    client = Client(api_key,user_key)
    with st.sidebar.form('search'):
        symbol = st.text_input('eToro-Symbol suchen',value='GOLD',help='Zum Beispiel das exakte Symbol aus deinem eToro-Chart. Varianten wie 24/7 gesondert auswählen.')
        search = st.form_submit_button('Instrument suchen / Verbindung testen')
    if search:
        if time.time() < st.session_state.get('search_after',0):
            st.warning('Bitte vor der nächsten Suche 10 Sekunden warten.')
        else:
            st.session_state.search_after = time.time()+10
            st.session_state.pop('matches',None)
            try:
                st.session_state.matches = client.search(symbol.strip())
                st.session_state.pop('bundle',None)
            except FeedError as e:
                st.error(str(e))
    matches = st.session_state.get('matches',[])
    if not matches:
        st.info('Links ein Symbol suchen. Danach den genauen Treffer auswählen. IDs werden nicht geraten.')
        st.stop()
    selected = st.sidebar.selectbox('Instrument bestätigen',range(len(matches)),format_func=lambda i:f"{matches[i]['internalSymbolFull']} · {matches[i].get('displayname','')} · ID {matches[i]['instrumentId']}")
    instrument_id = int(matches[selected]['instrumentId'])
    label = matches[selected]['internalSymbolFull']
    st.sidebar.caption('Nur Marktdaten. Keine Kontostände, Positionen oder Orderfunktionen.')
else:
    st.warning('DEMO: ausschließlich künstlich erzeugte Kurse. Keine eToro-Daten und keine Handelssignale.')

@st.fragment(run_every=60 if refresh else None)
def dashboard():
    st.subheader(label)
    force = st.button('Daten aktualisieren')
    key = (mode,instrument_id,count)
    cached = st.session_state.get('bundle')
    expired = not cached or cached['key'] != key or time.time()-cached['loaded'] >= 60
    if force and not expired:
        st.info('Der letzte Abruf ist jünger als 60 Sekunden. Vorhandene Daten werden angezeigt.')
    if expired:
        try:
            with st.spinner('Kerzen laden und prüfen …'):
                frames = demo_data(count) if client is None else {tf:client.candles(instrument_id,tf,count) for tf in TF}
            st.session_state.bundle = {'key':key,'loaded':time.time(),'frames':frames}
        except (FeedError,ValueError) as e:
            st.session_state.pop('bundle',None)
            st.error(str(e))
            st.info('Keine aktuelle Bewertung. Nach Behebung erneut laden.')
            return
    bundle = st.session_state.bundle
    frames = bundle['frames']
    if any(len(d)<40 for d in frames.values()):
        st.error('Zu wenig abgeschlossene Kerzen: mindestens 40 pro Zeitebene erforderlich.')
        return
    now = pd.Timestamp.now(tz='UTC')
    last_close_time = frames['5Min'].time.iloc[-1]+pd.Timedelta(minutes=5)
    stale_frames = [tf for tf,d in frames.items() if (now-(d.time.iloc[-1]+pd.Timedelta(minutes=TF[tf][1]))).total_seconds() > max(15,TF[tf][1]*2)*60]
    stale = client is not None and bool(stale_frames)
    price = float(frames['5Min'].close.iloc[-1])
    if stale:
        st.warning('Ältere Daten in '+', '.join(stale_frames)+': Marktpause oder verzögerter Feed möglich. Nur historische Zonen; keine aktuelle Reaktionsfreigabe.')
    st.caption(f"Letzter Abruf: {pd.Timestamp(bundle['loaded'],unit='s',tz='UTC').tz_convert('Europe/Berlin'):%d.%m.%Y %H:%M:%S} · Kerzenzeiten im Chart: UTC")
    tables, status = [], []
    atr_by_tf = {tf: float(atr(d).iloc[-1]) for tf, d in frames.items()}
    fingerprints = {tf: candle_fingerprint(d) for tf, d in frames.items()}
    for tf,d in frames.items():
        try:
            z = analyze(d)
        except ValueError as e:
            st.error(f'{tf}: {e}')
            return
        if not z.empty:
            z['Zeitebene'] = tf
            z['Bekannt_ab'] += pd.Timedelta(minutes=TF[tf][1])
            z['Ereigniszeit'] = pd.to_datetime(z.Ereigniszeit,utc=True)+pd.Timedelta(minutes=TF[tf][1])
            tables.append(z)
        gaps = int((d.time.diff()>pd.Timedelta(minutes=TF[tf][1]*1.5)).sum())
        status.append({'Zeitebene':tf,'Kerzen':len(d),'Beginn_UTC':d.time.iloc[0],'Letzter_Schluss_UTC':d.time.iloc[-1]+pd.Timedelta(minutes=TF[tf][1]),'Zeitlücken':gaps,'Daten_SHA256':fingerprints[tf]})
    all_z = pd.concat(tables,ignore_index=True) if tables else pd.DataFrame()
    cols = st.columns(3)
    cols[0].metric('Letzter 5Min-Schluss',f'{price:,.5f}')
    cols[1].metric('Zeitebenen','4')
    cols[2].metric('Datenstatus','Demo' if client is None else ('Historisch' if stale else 'Frisch nach Zeitprüfung'))
    st.caption(f'Kursstand: {last_close_time.tz_convert("Europe/Berlin"):%d.%m.%Y %H:%M} · kein ausführbarer Bid-/Ask-Kurs')
    if all_z.empty:
        st.info('Keine Zone nach den definierten Regeln gefunden.')
        all_z = pd.DataFrame(columns=['Typ','Seite','Untergrenze','Obergrenze','Status','Strukturpunkte','Tests','Abstand_ATR','Bekannt_ab','Letztes_Ereignis','Ereigniszeit','Begründung','Zeitebene'])
    all_z = zone_export_view(all_z, price, last_close_time, atr_by_tf)
    all_z['Daten_SHA256'] = all_z.Zeitebene.map(fingerprints)
    all_z['Referenzdaten_SHA256'] = fingerprints['5Min']
    all_z['Datenquelle'] = mode
    all_z['Instrument'] = label
    all_z['Instrument_ID'] = instrument_id
    all_z['Ansicht_Version'] = '0.3.0'
    indicators = {tf: indicator_series(d, stoch_length) for tf, d in frames.items()}
    summaries = {tf: indicator_summary(frames[tf], indicators[tf], TF[tf][1]) for tf in TF}
    all_z = add_zone_context(all_z, summaries, near_atr)
    all_z['Stoch_Parameter'] = f'{stoch_length}/3/3'
    context_table = pd.DataFrame([dict(Zeitebene=tf, **summaries[tf]) for tf in TF])
    overview, charttab, zonetab, data_tab = st.tabs(['Überblick','Chart','Alle Zonen','Daten & Methode'])
    with overview:
        st.markdown('**Indikatorkontext je Zeitebene**')
        st.dataframe(context_table[['Zeitebene','Kontextschluss_UTC','EMA_10','EMA_20','EMA_50','EMA_200','Stoch_K','Stoch_D','Stoch_Bereich','Stoch_Relation','Stoch_Kreuzung','ATR_14']], hide_index=True, width='stretch')
        st.caption(f'Stochastik {stoch_length}/3/3. Werte gelten zum jeweiligen Kerzenschluss (UTC). Oberhalb 80 / unterhalb 20 ist keine Umkehrbestätigung. EMA-Nähe ist eine räumliche Beobachtung, keine Trefferwahrscheinlichkeit.')
        if any(len(d) < 600 for d in frames.values()):
            st.info('EMA 200: unter 200 Kerzen kein Wert; unter 600 Kerzen begrenzter Vorlauf. Für mehr Vorlauf links 1000 Kerzen wählen. Werte können wegen Startwert und Historienlänge vom Brokerchart abweichen.')
        st.caption('Nächste gebündelte Bereiche aus allen vier Zeitebenen. Keine Trefferwahrscheinlichkeiten.')
        grouped = nearest_balanced(bundle_zones(all_z,price),6)
        if grouped.empty:
            st.info('Keine aktiven Zonen.')
        for _,r in grouped.iterrows():
            marker = '🟢' if r.Seite=='Unterstützung' else '🔴'
            with st.container(border=True):
                st.markdown(f"**{marker} {r.Zone} · {price_text(r.Untergrenze)}–{price_text(r.Obergrenze)}**")
                st.write(f"{r.Seite} · {r.Lage} · {r.Zeitebenen}")
                st.caption(f"{r.Typen} · {r.Einzelzonen} Einzelzonen · Abstand {price_text(r.Abstand_Preis)}")
                details = pd.DataFrame(r.Details)
                nearby = details[details.EMA_Zonenkontext.str.startswith('EMA')]
                if not nearby.empty:
                    descriptions = nearby[['Zeitebene','EMA_Zonenkontext']].drop_duplicates()
                    st.caption('Nähe zu Einzelzonen: ' + ' | '.join(f'{x.Zeitebene}: {x.EMA_Zonenkontext}' for x in descriptions.itertuples()))
                else:
                    st.caption('Keine verfügbare EMA innerhalb oder nahe den Einzelzonen.')
                with st.expander('Herkunft und Einzelbewertungen'):
                    st.dataframe(pd.DataFrame(r.Details)[['Zeitebene','Typ','Untergrenze','Obergrenze','Strukturpunkte','Tests','Bekannt_ab','Zonenbreite_ATR','EMA_Zonenkontext','Stoch_K','Stoch_D','Kontextschluss_UTC','Begründung']],hide_index=True,width='stretch')
        st.markdown('**Jüngste beobachtete Ereignisse**')
        events = all_z[all_z.Ereigniszeit.notna()].sort_values('Ereigniszeit',ascending=False).head(10)
        st.dataframe(events[['Zeitebene','Typ','Letztes_Ereignis','Ereigniszeit','Status']],hide_index=True,width='stretch')
        st.caption('Ereigniszeiten in UTC. Historische Beobachtungen, keine Kauf-/Verkaufsfreigaben.')
    with charttab:
        tf = st.selectbox('Chart-Zeitebene',list(TF),index=2)
        show_emas = st.multiselect('EMA-Linien', list(EMA_PERIODS), default=list(EMA_PERIODS))
        show_momentum = st.checkbox('Stochastik und ATR unter dem Chart', value=True)
        show_tfs = st.multiselect('Zonen aus',list(TF),default=[tf])
        kinds = st.multiselect('Zonentypen',['Unterstützung/Widerstand','Angebot/Nachfrage','Liquidität vermutet'],default=['Unterstützung/Widerstand','Angebot/Nachfrage','Liquidität vermutet'])
        bundled = st.checkbox('Überlappende Zonen bündeln',value=True)
        max_zones = st.slider('Sichtbare Bereiche',3,12,6)
        bars = st.select_slider('Kerzen im Chart',options=[40,80,120,180,300],value=80)
        d = frames[tf].tail(bars)
        end = d.time.iloc[-1]+pd.Timedelta(minutes=TF[tf][1])
        # Exclude zones only discovered AFTER the selected chart's last closed bar.
        # Current status is still evaluated to each source timeframe's latest bar.
        eligible = all_z[(all_z.Status!='Gebrochen')&all_z.Zeitebene.isin(show_tfs)&all_z.Typ.isin(kinds)&(all_z.Bekannt_ab<=end)].copy()
        chart_price = price
        if bundled:
            candidates=bundle_zones(eligible,chart_price)
        else:
            parts=[]
            for _,row in eligible.iterrows():
                parts.append(bundle_zones(pd.DataFrame([row]),chart_price))
            candidates=pd.concat(parts,ignore_index=True).sort_values('Abstand_Preis',kind='stable').reset_index(drop=True) if parts else pd.DataFrame()
            if not candidates.empty:
                candidates['Zone']=[f'Z{i+1}' for i in range(len(candidates))]
        visible=nearest_balanced(candidates,max_zones)
        fig = make_subplots(rows=3 if show_momentum else 1, cols=1, shared_xaxes=True,
                            row_heights=[0.62,0.23,0.15] if show_momentum else [1], vertical_spacing=0.04)
        fig.add_trace(go.Candlestick(x=d.time,open=d.open,high=d.high,low=d.low,close=d.close,name=label,increasing_line_color='#36cbb0',decreasing_line_color='#f67b88'), row=1,col=1)
        ind = indicators[tf].loc[d.index]
        colors = {10:'#32cbd5',20:'#ffab40',50:'#c58cff',200:'#f2f3f5'}
        for n in show_emas:
            fig.add_trace(go.Scatter(x=d.time,y=ind[f'EMA_{n}'],name=f'EMA {n}',mode='lines',line=dict(color=colors[n],width=1.5),connectgaps=False),row=1,col=1)
        if show_momentum:
            fig.add_trace(go.Scatter(x=d.time,y=ind.Stoch_K,name='Stoch K',line=dict(color='#32cbd5'),connectgaps=False),row=2,col=1)
            fig.add_trace(go.Scatter(x=d.time,y=ind.Stoch_D,name='Stoch D',line=dict(color='#ffab40'),connectgaps=False),row=2,col=1)
            for level in [20,80]:
                fig.add_hline(y=level,line_dash='dash',line_color='#8491a7',row=2,col=1)
            fig.update_yaxes(range=[0,100],title_text='Stoch',row=2,col=1)
            fig.add_trace(go.Scatter(x=d.time,y=ind.ATR_14,name='ATR 14 (SMA)',line=dict(color='#f67b88'),connectgaps=False),row=3,col=1)
            fig.update_yaxes(title_text='ATR',row=3,col=1)
        for _,r in visible.iterrows():
            color = '#36cbb0' if r.Seite=='Unterstützung' else '#f67b88'
            start = max(d.time.iloc[0],r.Bekannt_ab)
            fig.add_shape(type='rect',x0=start,x1=end,y0=r.Untergrenze,y1=r.Obergrenze,line=dict(color=color,width=1),fillcolor=color,opacity=.16,row=1,col=1)
            fig.add_annotation(x=end,y=(r.Untergrenze+r.Obergrenze)/2,text=f"{r.Zone} {price_text(r.Untergrenze)}–{price_text(r.Obergrenze)}",showarrow=False,xanchor='right',font=dict(size=10,color=color),bgcolor='#0b1120',row=1,col=1)
        fig.update_layout(height=800 if show_momentum else 540,template='plotly_dark',paper_bgcolor='#0b1120',plot_bgcolor='#0b1120',xaxis_rangeslider_visible=False,margin=dict(l=5,r=5,t=15,b=10),yaxis_title='Kurs',xaxis_title='UTC',dragmode='pan')
        fig.update_xaxes(rangeslider_visible=False)
        st.plotly_chart(fig,width='stretch',config={'displaylogo':False,'scrollZoom':False,'modeBarButtonsToRemove':['lasso2d','select2d']})
        st.caption('EMA, Stochastik und ATR verwenden die gesamte geladene Historie der Chart-Zeitebene; die Anzahl sichtbarer Kerzen verändert die Berechnung nicht.')
        st.caption('Grün/Rot = ursprüngliche Unterstützungs-/Widerstandsseite. Referenz für Bereichsauswahl und Abstände: letzter 5Min-Schluss. Kein Einstiegssignal.')
        if not visible.empty:
            st.dataframe(visible[['Zone','Untergrenze','Obergrenze','Seite','Zeitebenen','Typen','Einzelzonen']],hide_index=True,width='stretch')
        st.caption('Bündel umfassen nur Bereiche derselben Seite mit gemeinsamer Preisüberschneidung. Ihre volle Breite wird erst ab der spätesten Bestätigung gezeichnet. Darstellung des heutigen Zustands, kein historischer Replay.')
    with zonetab:
        st.caption('Beide Abstände verwenden den letzten 5Min-Schluss. Abstand_ATR = Abstand_Preis / ATR_14_TF (ATR der Zonen-Zeitebene). Überlappung_TF zählt nur aktive Zonen gleicher Seite; keine unabhängige Bestätigung.')
        include_broken = st.checkbox('Gebrochene Zonen anzeigen')
        out = all_z if include_broken else all_z[all_z.Status!='Gebrochen']
        st.dataframe(out.sort_values('Abstand_Preis'),hide_index=True,width='stretch')
        st.download_button('Zonen als CSV',out.to_csv(index=False).encode('utf-8-sig'),file_name='zonen.csv',mime='text/csv')
    with data_tab:
        st.markdown('**Kerzenvergleich mit eToro**')
        compare_tf = st.selectbox('Zeitebene für Kerzentabelle',list(TF),key='compare_tf')
        candle_rows = candle_table(frames[compare_tf],TF[compare_tf][1])
        st.caption('Beginn und rechnerisches Intervallende; Europe/Berlin berücksichtigt Sommer-/Winterzeit. Bei verkürzten Sessions kann das tatsächliche Ende abweichen. Nur abgeschlossene Kerzen, keine Bid-/Ask-Livekurse.')
        st.dataframe(candle_rows.tail(30).iloc[::-1],hide_index=True,width='stretch')
        st.download_button('Alle geladenen Kerzen dieser Zeitebene als CSV',candle_rows.to_csv(index=False).encode('utf-8-sig'),file_name=f'kerzen_{compare_tf}.csv',mime='text/csv')
        st.markdown('**Vergleichbare Datenstände**')
        st.caption('SHA-256 kennzeichnet die geladenen Kerzen je Zeitebene. Andere Historienlänge oder geänderte Kerzen ergeben einen anderen Fingerabdruck. Der Sichtbarkeitsschalter ändert die Erkennung nicht; nach 60 Sekunden kann eine Bedienaktion jedoch einen neuen Abruf auslösen.')
        import io
        import json
        import zipfile
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as snapshot:
            for source_tf, source_frame in frames.items():
                snapshot.writestr(f'kerzen_{source_tf}.csv', source_frame[['time','open','high','low','close']].to_csv(index=False, float_format='%.17g'))
            snapshot.writestr('indikatoren_aktuell.csv', context_table.to_csv(index=False))
            snapshot.writestr('zonen_alle.csv', all_z.to_csv(index=False))
            snapshot.writestr('metadaten.json', json.dumps({
                'ansicht_version': '0.3.0', 'datenquelle': mode,
                'instrument': label, 'instrument_id': instrument_id,
                'abgerufen_utc': pd.Timestamp(bundle['loaded'], unit='s', tz='UTC').isoformat(),
                'referenzpreis': price, 'referenzschluss_utc': last_close_time.isoformat(),
                'kerzen_sha256': fingerprints, 'atr_14_tf': atr_by_tf,
                'indikatoren': {'ema_perioden': list(EMA_PERIODS), 'ema_start': 'erster geladener Schluss; adjust=False; min_periods=Periode', 'stoch': [stoch_length,3,3], 'atr': 'TR SMA 14', 'ema_naehe_atr': near_atr},
                'hinweis': 'Erkennung gemäß bereitgestellter engine.py; kein persistentes Archiv.'
            }, ensure_ascii=False, indent=2))
        st.download_button('Datenstand als ZIP sichern', archive.getvalue(), file_name='zonen_datenstand.zip', mime='application/zip')
        st.markdown('**Feed-Zeiträume**')
        st.dataframe(pd.DataFrame(status),hide_index=True,width='stretch')
        st.markdown('''**Indikatorkontext (separat von Strukturpunkten)**
- EMA 10/20/50/200: rekursiv mit α = 2/(Periode+1), Start beim ersten geladenen Schluss. Anzeige erst nach mindestens einer Periodenlänge; keine Rückwärtsauffüllung. EMA 200 bei weniger als 600 Kerzen als begrenzter Vorlauf gekennzeichnet.
- Stochastik: 100 × (Schluss − tiefstes Tief)/(höchstes Hoch − tiefstes Tief) im gewählten Lookback; K und D jeweils 3-fach einfach geglättet. Bei Nullspanne fehlt der Wert. Kreuzung nur zwischen den letzten zwei abgeschlossenen Kerzen.
- ATR bleibt der einfache Mittelwert der True Range über 14 Kerzen; kein Wilder-ATR. Zonenbreite_ATR = Breite / aktueller ATR der Zonen-Zeitebene.
- EMA-Abstand ist der kürzeste Abstand zur Zonengrenze, innerhalb der Zone null. EMA und ATR stammen aus derselben Zeitebene wie die Einzelzone. Nähe ist über die frei einstellbare ATR-Schwelle definiert.
- Kontext ist eine aktuelle Momentaufnahme, keine Rekonstruktion zum Entstehungszeitpunkt. Zeitebenen können unterschiedliche letzte Schlusszeiten haben. Gebrochene Zonen erhalten keinen aktiven EMA-Zonenkontext.
- EMA-Übereinstimmungen werden nicht zu Punkten oder Wahrscheinlichkeiten addiert. Mehrere EMAs beruhen auf denselben Kursdaten; ihre Nähe ist keine unabhängige Bestätigung.
''')
        st.markdown('''**Erkennungsregeln (unverändert)**
- Wendepunkte: je drei Kerzen links und rechts; bekannt erst nach Schluss der dritten Folgekerze.
- Zonenbreite an Wendepunkten: ±0,18 ATR(14); ATR hier als gleitender Mittelwert der True Range.
- Angebot/Nachfrage: drei kompakte Kerzen (Gesamtspanne ≤1,2 ATR), danach Impulskörper ≥1,2 ATR und Schluss ≥0,3 ATR außerhalb der Basis.
- Vermutete Liquidität: zwei bestätigte ähnliche Extrempunkte, Abstand ≤0,25 ATR. Kein Nachweis offener Stops.
- Tests: zusammenhängende Berührungen zählen als ein Besuch. Zwei Schlüsse >0,1 ATR jenseits der ungünstigen Grenze markieren einen dauerhaften Bruch.
- Strukturpunkte: Basis 40 + Reaktions-/Impulsstärke (max. 36) + 10 für Basis-/Liquiditätsmuster, abzüglich Wiederholungstests und Alter. Nicht statistisch kalibriert.
- Überlappende Zonen derselben Seite werden optional für die Anzeige gebündelt. Keine Addition von Punkten oder Tests; Einzelbegründungen bleiben einsehbar. Keine Verkettung ohne gemeinsame Preisüberschneidung.
- Nur abgeschlossene Kerzen; originale eToro-Zeitstempel. Keine automatische Brokerzeit-Umrechnung oder synthetische 4H-Kerzen im API-Modus.
- Zeitlücken können Handelsunterbrechungen oder fehlende Daten sein. Es gibt noch keinen instrumentbezogenen Handelskalender.

**Grenzen:** Kein Orderbuch, kein echtes Volumenprofil, keine Performance- oder Profitabilitätsaussage. Keine automatischen Trades. Aktuell kein persistentes Archiv, kein vollständiger Backtest, keine Rollenwechsel- oder Retest-Bestätigung nach einem Bruch. API-Zugang und Datenumfang müssen mit deinem Konto geprüft werden.''')

dashboard()
