import hmac
import time
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from engine import TF, analyze, demo_data, atr, discover
from etoro import Client, FeedError
from zone_view import candle_table, bundle_zones, nearest_balanced, price_text, candle_fingerprint, zone_export_view

from zone_chart import typed_groups, confirmed_swing, decorate, fullscreen_html, decorate_sweeps
from zone_context import EMA_PERIODS, indicator_series, indicator_summary, add_zone_context
from zone_sweeps import detect_all_sweeps
from zone_quality import assess_zones, QUALITY_COLUMNS
from zone_time import TIMEZONES, format_time, display_table, axis_ticks, label as time_label

st.set_page_config(page_title='Zonenradar · eToro',page_icon='◈',layout='wide')
st.markdown('''<style>
.stApp {background: #0b1120;} [data-testid="stMetric"] {background:#142033;padding:16px;border-radius:12px;}
@media(max-width:600px){.block-container{padding-left:.25rem;padding-right:.25rem;} } h1 {letter-spacing:-1px;} .block-container {padding-top:2rem;}
</style>''',unsafe_allow_html=True)
st.title('Zonenradar')
st.caption('eToro · 1D / 4H / 1H / 15Min / 5Min · Ansicht 0.4.2')

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
    min_quality = st.slider('Mindestqualität für Übersicht und Chart',0,90,55,5,help='Heuristische 0–100-Punkteskala, keine Gewinnchance. 0 zeigt alle aktiven Zonen. Die vollständige Zonenausgabe bleibt erhalten.')
    time_choice = st.selectbox('Zeitanzeige',TIMEZONES,index=1,help='UTC oder deutsche Ortszeit. MEZ/MESZ wird automatisch nach Datum gewechselt. Kursberechnungen und Rohdaten bleiben UTC.')
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
    st.caption(f"Letzter Abruf: {format_time(pd.Timestamp(bundle['loaded'],unit='s',tz='UTC'), time_choice)} · Zeitanzeige: {time_label(time_choice)}")
    tables, status = [], []
    atr_by_tf = {tf: float(atr(d).iloc[-1]) for tf, d in frames.items()}
    fingerprints = {tf: candle_fingerprint(d) for tf, d in frames.items()}
    zones_by_tf = {}
    for tf,d in frames.items():
        try:
            z = analyze(d)
            zones_by_tf[tf] = discover(d)
            if len(z) != len(zones_by_tf[tf]):
                raise ValueError('Interne Zonenreihenfolge ist nicht konsistent.')
            quality = assess_zones(d, zones_by_tf[tf], z.to_dict('records'))
            for quality_col in QUALITY_COLUMNS:
                z[quality_col] = quality[quality_col].to_numpy()
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
    all_sweeps = detect_all_sweeps(frames, zones_by_tf)
    if not all_sweeps.empty:
        all_sweeps['Datenquelle'] = mode
        all_sweeps['Instrument'] = label
        all_sweeps['Daten_SHA256'] = all_sweeps.Zeitebene.map(fingerprints)
    all_z = pd.concat(tables,ignore_index=True) if tables else pd.DataFrame()
    cols = st.columns(3)
    cols[0].metric('Letzter 5Min-Schluss',f'{price:,.5f}')
    cols[1].metric('Zeitebenen',str(len(TF)))
    cols[2].metric('Datenstatus','Demo' if client is None else ('Historisch' if stale else 'Frisch nach Zeitprüfung'))
    st.caption(f'Kursstand: {format_time(last_close_time, time_choice)} · kein ausführbarer Bid-/Ask-Kurs')
    if all_z.empty:
        st.info('Keine Zone nach den definierten Regeln gefunden.')
        all_z = pd.DataFrame(columns=['Typ','Seite','Untergrenze','Obergrenze','Status','Strukturpunkte','Tests','Abstand_ATR','Bekannt_ab','Letztes_Ereignis','Ereigniszeit','Begründung','Zeitebene',*QUALITY_COLUMNS])
    all_z = zone_export_view(all_z, price, last_close_time, atr_by_tf)
    all_z['Daten_SHA256'] = all_z.Zeitebene.map(fingerprints)
    all_z['Referenzdaten_SHA256'] = fingerprints['5Min']
    all_z['Datenquelle'] = mode
    all_z['Instrument'] = label
    all_z['Instrument_ID'] = instrument_id
    all_z['Ansicht_Version'] = '0.4.2'
    indicators = {tf: indicator_series(d, stoch_length) for tf, d in frames.items()}
    summaries = {tf: indicator_summary(frames[tf], indicators[tf], TF[tf][1]) for tf in TF}
    all_z = add_zone_context(all_z, summaries, near_atr)
    all_z['Stoch_Parameter'] = f'{stoch_length}/3/3'
    context_table = pd.DataFrame([dict(Zeitebene=tf, **summaries[tf]) for tf in TF])
    focused_z = all_z[(all_z.Status != 'Gebrochen') & (all_z.Qualitaet_Punkte >= min_quality)].copy()
    overview, charttab, zonetab, data_tab = st.tabs(['Überblick','Chart','Alle Zonen','Daten & Methode'])
    with overview:
        st.markdown('**Indikatorkontext je Zeitebene**')
        st.dataframe(display_table(context_table[['Zeitebene','Kontextschluss_UTC','EMA_10','EMA_20','EMA_50','EMA_200','Stoch_K','Stoch_D','Stoch_Bereich','Stoch_Relation','Stoch_Kreuzung','ATR_14']], ['Kontextschluss_UTC'], time_choice), hide_index=True, width='stretch')
        st.caption(f'Stochastik {stoch_length}/3/3. Werte gelten zum jeweiligen abgeschlossenen Kerzenschluss ({time_label(time_choice)}). Oberhalb 80 / unterhalb 20 ist keine Umkehrbestätigung. EMA-Nähe ist eine räumliche Beobachtung, keine Trefferwahrscheinlichkeit.')
        if any(len(d) < 600 for d in frames.values()):
            st.info('EMA 200: unter 200 Kerzen kein Wert; unter 600 Kerzen begrenzter Vorlauf. Für mehr Vorlauf links 1000 Kerzen wählen. Werte können wegen Startwert und Historienlänge vom Brokerchart abweichen.')
        st.caption(f'Aktive Zonen ab {min_quality}/100 Qualität: {len(focused_z)} von {int((all_z.Status != "Gebrochen").sum())}. Aus allen fünf Zeitebenen; Punkte sind technische Heuristik, keine statistische Trefferwahrscheinlichkeit.')
        grouped = nearest_balanced(bundle_zones(focused_z,price),6)
        if grouped.empty:
            st.info('Keine aktiven Zonen.')
        for _,r in grouped.iterrows():
            marker = '🟢' if r.Seite=='Unterstützung' else '🔴'
            with st.container(border=True):
                st.markdown(f"**{marker} {r.Zone} · {price_text(r.Untergrenze)}–{price_text(r.Obergrenze)}**")
                st.write(f"{r.Seite} · {r.Lage} · {r.Zeitebenen}")
                st.caption(f"{r.Typen} · {r.Einzelzonen} Einzelzonen · beste Einzelqualität {r.Qualitaet_Beste}/100 · Abstand {price_text(r.Abstand_Preis)}")
                details = pd.DataFrame(r.Details)
                nearby = details[details.EMA_Zonenkontext.str.startswith('EMA')]
                if not nearby.empty:
                    descriptions = nearby[['Zeitebene','EMA_Zonenkontext']].drop_duplicates()
                    st.caption('Nähe zu Einzelzonen: ' + ' | '.join(f'{x.Zeitebene}: {x.EMA_Zonenkontext}' for x in descriptions.itertuples()))
                else:
                    st.caption('Keine verfügbare EMA innerhalb oder nahe den Einzelzonen.')
                with st.expander('Herkunft und Einzelbewertungen'):
                    detail_cols = ['Zeitebene','Typ','Untergrenze','Obergrenze','Qualitaet_Punkte','Qualitaet_Klasse','Abweisungen_geprueft','Abweisungen_ab_0_5_ATR','Mittlere_Abweisung_ATR','Tests','Bekannt_ab','Zonenbreite_ATR','EMA_Zonenkontext','Stoch_K','Stoch_D','Kontextschluss_UTC','Begründung']
                    st.dataframe(display_table(pd.DataFrame(r.Details)[detail_cols], ['Bekannt_ab','Kontextschluss_UTC'], time_choice),hide_index=True,width='stretch')
        st.markdown('**Jüngste beobachtete Ereignisse**')
        events = all_z[all_z.Ereigniszeit.notna()].sort_values('Ereigniszeit',ascending=False).head(10)
        st.dataframe(display_table(events[['Zeitebene','Typ','Letztes_Ereignis','Ereigniszeit','Status']], ['Ereigniszeit'], time_choice),hide_index=True,width='stretch')
        st.caption(f'Ereignisse in {time_label(time_choice)}. Historische Beobachtungen, keine Kauf-/Verkaufsfreigaben.')
        st.markdown('**Liquidity Sweep → Rückeroberung → möglicher Folgeimpuls**')
        if all_sweeps.empty:
            st.info('Im geladenen Zeitraum keine Rückeroberung nach den definierten Sweep-Regeln gefunden.')
        else:
            show_sweep_table = all_sweeps.sort_values('Rueckeroberung_UTC', ascending=False).head(20)
            st.dataframe(display_table(show_sweep_table[['Zeitebene','Typ','Seite','Sweep_UTC','Rueckeroberung_UTC',
                                           'Bestaetigung_UTC','Durchbruch_ATR','Status']],
                          ['Sweep_UTC','Rueckeroberung_UTC','Bestaetigung_UTC'], time_choice),
                         hide_index=True,width='stretch')
        st.caption('Nur bereits bestätigte Zonen und abgeschlossene Kerzen; Folgeimpuls ist kein Beweis eines neuen Trends. Gleiche Bewegungen können an mehreren Einzelzonen erscheinen.')
    with charttab:
        tf = st.selectbox('Chart-Zeitebene',list(TF),index=list(TF).index('15Min'))
        st.caption(f'Chartzeit: {time_label(time_choice)} · Qualitätsfilter: mindestens {min_quality}/100. Auswahl in der Seitenleiste.')
        with st.expander('Chart-Einstellungen · Zonen · Fibonacci · Sweeps', expanded=False):
            show_emas = st.multiselect('EMA-Linien', list(EMA_PERIODS), default=list(EMA_PERIODS))
            show_momentum = st.checkbox('Stochastik und ATR unter dem Chart', value=True)
            show_sweeps = st.checkbox('Sweep-Rückeroberungen markieren', value=True)
            show_tfs = st.multiselect('Zonen aus',list(TF),default=[tf])
            kinds = st.multiselect('Zonentypen',['Unterstützung/Widerstand','Angebot/Nachfrage','Liquidität vermutet'],default=['Unterstützung/Widerstand','Angebot/Nachfrage','Liquidität vermutet'])
            bundled = st.checkbox('Überlappende Zonen bündeln',value=True)
            max_zones = st.slider('Sichtbare Bereiche',3,12,6)
            bars = st.select_slider('Kerzen im Chart',options=[40,80,120,180,300],value=80)
            fib_mode = st.selectbox('Fibonacci', ['Letzter bestätigter Schwung','Manuelle Preise','Aus'])
            fib_start = st.number_input('Fibonacci Startpreis', value=float(frames[tf].low.tail(80).min()), format='%.5f', key=f'fib_start_{tf}') if fib_mode == 'Manuelle Preise' else None
            fib_end = st.number_input('Fibonacci Endpreis', value=float(frames[tf].high.tail(80).max()), format='%.5f', key=f'fib_end_{tf}') if fib_mode == 'Manuelle Preise' else None
        d = frames[tf].tail(bars)
        end = d.time.iloc[-1]+pd.Timedelta(minutes=TF[tf][1])
        # Exclude zones only discovered AFTER the selected chart's last closed bar.
        # Current status is still evaluated to each source timeframe's latest bar.
        eligible = focused_z[focused_z.Zeitebene.isin(show_tfs)&focused_z.Typ.isin(kinds)&(focused_z.Bekannt_ab<=end)].copy()
        chart_price = price
        candidates = typed_groups(eligible, chart_price, bundled)
        visible=nearest_balanced(candidates,max_zones)
        fig = make_subplots(rows=3 if show_momentum else 1, cols=1, shared_xaxes=True,
                            row_heights=[0.62,0.23,0.15] if show_momentum else [1], vertical_spacing=0.04)
        chart_times = [format_time(t, time_choice) for t in d.time]
        candle_hover = [f'{label}<br>{t}<br>O: {r.open:.5f} · H: {r.high:.5f}<br>L: {r.low:.5f} · C: {r.close:.5f}' for t, r in zip(chart_times, d.itertuples())]
        fig.add_trace(go.Candlestick(x=d.time,open=d.open,high=d.high,low=d.low,close=d.close,name=label,showlegend=False,hovertext=candle_hover,hoverinfo='text',increasing_line_color='#36cbb0',decreasing_line_color='#f67b88'), row=1,col=1)
        ind = indicators[tf].loc[d.index]
        colors = {10:'#32cbd5',20:'#ffab40',50:'#c58cff',200:'#f2f3f5'}
        for n in show_emas:
            fig.add_trace(go.Scatter(x=d.time,y=ind[f'EMA_{n}'],name=f'EMA {n}',mode='lines',line=dict(color=colors[n],width=1.5),connectgaps=False,customdata=chart_times,hovertemplate=f'EMA {n}<br>%{{customdata}}<br>%{{y:.5f}}<extra></extra>'),row=1,col=1)
        if show_momentum:
            fig.add_trace(go.Scatter(x=d.time,y=ind.Stoch_K,name='Stoch K',showlegend=False,line=dict(color='#32cbd5'),connectgaps=False,customdata=chart_times,hovertemplate='Stoch K<br>%{customdata}<br>%{y:.4f}<extra></extra>'),row=2,col=1)
            fig.add_trace(go.Scatter(x=d.time,y=ind.Stoch_D,name='Stoch D',showlegend=False,line=dict(color='#ffab40'),connectgaps=False,customdata=chart_times,hovertemplate='Stoch D<br>%{customdata}<br>%{y:.4f}<extra></extra>'),row=2,col=1)
            for level in [20,80]:
                fig.add_hline(y=level,line_dash='dash',line_color='#8491a7',row=2,col=1)
            fig.update_yaxes(range=[0,100],title_text='Stoch',row=2,col=1)
            fig.add_trace(go.Scatter(x=d.time,y=ind.ATR_14,name='ATR 14',showlegend=False,line=dict(color='#f67b88'),connectgaps=False,customdata=chart_times,hovertemplate='ATR 14<br>%{customdata}<br>%{y:.6f}<extra></extra>'),row=3,col=1)
            fig.update_yaxes(title_text='ATR',row=3,col=1)
        fig.update_layout(height=800 if show_momentum else 540,template='plotly_dark',paper_bgcolor='#0b1120',plot_bgcolor='#0b1120',xaxis_rangeslider_visible=False,margin=dict(l=5,r=5,t=15,b=10),yaxis_title='Kurs',dragmode='pan')
        ticks, tick_labels = axis_ticks(d.time, time_choice, maximum=9)
        fig.update_xaxes(rangeslider_visible=False, tickmode='array',tickvals=ticks,ticktext=tick_labels,tickfont=dict(size=10))
        fig.update_xaxes(title_text=f'Zeit · {time_label(time_choice)}', row=3 if show_momentum else 1, col=1)
        fib = None
        if fib_mode == 'Letzter bestätigter Schwung':
            anchors = confirmed_swing(frames[tf])
            if anchors:
                fib = (anchors[0][1], anchors[1][1])
                st.caption(f'Fibonacci: {fib[0]:.5f} ({format_time(anchors[0][2], time_choice, compact=True)}) → {fib[1]:.5f} ({format_time(anchors[1][2], time_choice, compact=True)}). 0% am Ende, 100% am Start.')
            else:
                st.info('Noch kein eindeutiges bestätigtes Fibonacci-Ankerpaar.')
        elif fib_mode == 'Manuelle Preise':
            if fib_start != fib_end:
                fib = (fib_start, fib_end)
            else:
                st.warning('Bitte unterschiedliche Fibonacci-Preise eingeben.')
        fig = decorate(fig, visible, d.time.iloc[0], end, fib)
        if show_sweeps:
            approved_ids = set(focused_z.loc[focused_z.Zeitebene==tf, 'Zonen_ID'])
            filtered_sweeps = all_sweeps[(all_sweeps.Zeitebene == tf) & all_sweeps.Typ.isin(kinds) & all_sweeps.Zonen_ID.isin(approved_ids)]
            fig = decorate_sweeps(fig, filtered_sweeps, d.time.iloc[0], end, TF[tf][1], time_choice=time_choice)
        if show_momentum:
            for axis, value in [('y2', f'Stoch K {ind.Stoch_K.iloc[-1]:.4f} · D {ind.Stoch_D.iloc[-1]:.4f}'), ('y3', f'ATR(14) {ind.ATR_14.iloc[-1]:.6f}')]:
                fig.add_annotation(x=0,y=1,xref='paper',yref=f'{axis} domain',text=value,showarrow=False,xanchor='left',yanchor='top',bgcolor='#0b1120',font=dict(size=11))
        st.caption(f'Letzter abgeschlossener {tf}-Wert · ATR(14): {ind.ATR_14.iloc[-1]:.6f} · Stoch K: {ind.Stoch_K.iloc[-1]:.4f} · D: {ind.Stoch_D.iloc[-1]:.4f} · {format_time(end, time_choice)}')
        st.caption('Vollbild: auf das Vergrößern-Symbol oben rechts am Chart tippen. Legende oben; Antippen blendet Linien aus.')
        st.plotly_chart(fig,width='stretch',config={'displaylogo':False,'scrollZoom':False,'modeBarButtonsToRemove':['lasso2d','select2d']})
        st.caption('EMA, Stochastik und ATR verwenden die gesamte geladene Historie der Chart-Zeitebene; die Anzahl sichtbarer Kerzen verändert die Berechnung nicht.')
        st.download_button('Bildschirmfüllenden Chart als HTML speichern', fullscreen_html(fig).encode('utf-8'), file_name='zonen_chart.html', mime='text/html')
        st.caption('Sweep-Rückeroberung: Marker folgen dem aktuellen Qualitätsfilter und zeigen nur Zonen, die heute noch aktiv sind. Grünes Dreieck für Long-Reaktion, rotes Dreieck für Short-Reaktion. Die Dreiecke sind keine Handelssignale. Der angezeigte Ereignisstatus kann auf später geschlossenen Kerzen beruhen (kein historischer Replay).')
        st.caption('Blau: Unterstützung/Widerstand · Gold: Angebot/Nachfrage · Violett: vermutete Liquidität. Durchgezogen: Unterstützung; gestrichelt: Widerstand. HTML im Browser öffnen: passt sich dem Fenster an, auch im Querformat. Statischer Datenstand ohne Live-Aktualisierung.')
        if not visible.empty:
            st.dataframe(visible[['Zone','Untergrenze','Obergrenze','Seite','Zeitebenen','Typen','Einzelzonen']],hide_index=True,width='stretch')
        st.caption('Chart-Bündel umfassen nur Bereiche desselben Typs und derselben Seite mit gemeinsamer Preisüberschneidung. Die Anzeige zeigt ausschließlich Zonen über dem Mindestqualitätswert. Ihre volle Breite wird erst ab der spätesten Bestätigung gezeichnet. Darstellung des heutigen Zustands, kein historischer Replay.')
    with zonetab:
        st.caption(f'Alle entdeckten Zonen unabhängig vom Qualitätsfilter, Anzeigezeiten {time_label(time_choice)}. Beide Abstände verwenden den letzten 5Min-Schluss. Abstand_ATR = Abstand_Preis / ATR_14_TF (ATR der Zonen-Zeitebene). Überlappung_TF zählt nur aktive Zonen gleicher Seite; keine unabhängige Bestätigung.')
        include_broken = st.checkbox('Gebrochene Zonen anzeigen')
        out = all_z if include_broken else all_z[all_z.Status!='Gebrochen']
        st.dataframe(display_table(out.sort_values('Abstand_Preis'), ['Bekannt_ab','Ereigniszeit','Referenzschluss_UTC','Kontextschluss_UTC'],time_choice),hide_index=True,width='stretch')
        st.download_button('Zonen als CSV',out.to_csv(index=False).encode('utf-8-sig'),file_name='zonen.csv',mime='text/csv')
        st.markdown('**Sweep-Ereignisse (unabhängig vom heutigen Zonenstatus)**')
        st.dataframe(display_table(all_sweeps.sort_values('Rueckeroberung_UTC',ascending=False), ['Bekannt_ab_UTC','Sweep_UTC','Rueckeroberung_UTC','Bestaetigung_UTC','Gebrochen_UTC'],time_choice),hide_index=True,width='stretch')
        st.download_button('Sweep-Ereignisse als CSV',all_sweeps.to_csv(index=False).encode('utf-8-sig'),file_name='zone_sweeps.csv',mime='text/csv')
    with data_tab:
        st.markdown('**Kerzenvergleich mit eToro**')
        compare_tf = st.selectbox('Zeitebene für Kerzentabelle',list(TF),key='compare_tf')
        candle_rows = candle_table(frames[compare_tf],TF[compare_tf][1])
        st.caption(f'Beginn und rechnerisches Intervallende in {time_label(time_choice)}. Deutsche Zeit berücksichtigt Sommer-/Winterzeit. Bei verkürzten Sessions kann das tatsächliche Ende abweichen. Nur abgeschlossene Kerzen, keine Bid-/Ask-Livekurse.')
        begin_col, end_col = ('Beginn_UTC','Schluss_UTC') if time_choice == TIMEZONES[0] else ('Beginn_Berlin','Schluss_Berlin')
        selected_candles = candle_rows[[begin_col,end_col,'Open','High','Low','Close']].rename(columns={begin_col:f'Beginn [{time_label(time_choice)}]',end_col:f'Schluss [{time_label(time_choice)}]'})
        st.dataframe(selected_candles.tail(30).iloc[::-1],hide_index=True,width='stretch')
        st.download_button('Alle geladenen Kerzen dieser Zeitebene als CSV (UTC und Berlin)',candle_rows.to_csv(index=False).encode('utf-8-sig'),file_name=f'kerzen_{compare_tf}.csv',mime='text/csv')
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
            snapshot.writestr('sweep_ereignisse.csv', all_sweeps.to_csv(index=False))
            snapshot.writestr('metadaten.json', json.dumps({
                'ansicht_version': '0.4.2', 'datenquelle': mode, 'zeitdarstellung': time_choice, 'kanonische_zeit': 'UTC',
                'instrument': label, 'instrument_id': instrument_id,
                'abgerufen_utc': pd.Timestamp(bundle['loaded'], unit='s', tz='UTC').isoformat(),
                'referenzpreis': price, 'referenzschluss_utc': last_close_time.isoformat(),
                'kerzen_sha256': fingerprints, 'atr_14_tf': atr_by_tf,
                'indikatoren': {'ema_perioden': list(EMA_PERIODS), 'ema_start': 'erster geladener Schluss; adjust=False; min_periods=Periode', 'stoch': [stoch_length,3,3], 'atr': 'TR SMA 14', 'ema_naehe_atr': near_atr},
                'sweeps': {'reclaim_kerzen': 3, 'impuls_kerzen': 5, 'min_tiefe_atr': 0.05, 'impuls_puffer_atr': 0.10, 'referenz_kerzen': 3},
                'qualitaetsfilter': {'mindestens': min_quality, 'punkte_heuristik': True, 'zeitfenster_nach_kontakt': 3, 'abweisungsschwelle_atr': 0.5, 'zonen_csv_immer_vollstaendig': True},
                'hinweis': 'Basiszonen unverändert nach engine.py, Qualität ist nachträgliche deskriptive Bewertung; keine Trefferwahrscheinlichkeit, keine vollständige Replayprüfung. Anzeige umstellbar; Rohdaten UTC. Kein persistentes Archiv.'
            }, ensure_ascii=False, indent=2))
        st.download_button('Datenstand als ZIP sichern', archive.getvalue(), file_name='zonen_datenstand.zip', mime='application/zip')
        st.markdown('**Feed-Zeiträume**')
        st.dataframe(display_table(pd.DataFrame(status), ['Beginn_UTC','Letzter_Schluss_UTC'],time_choice),hide_index=True,width='stretch')
        st.markdown('''**Zonenverfeinerung und Qualitätsfilter (0.4.2)**
- Alle ursprünglichen, kausal bestätigten Zonen aus engine.py bleiben erhalten. Filter beeinflussen nur die aktuelle Übersicht, Chartzonen und Marker aktiver Zonen; „Alle Zonen“, vollständige Sweep-Liste sowie CSV/ZIP enthalten alle gefundenen Bereiche.
- Die zusätzliche Qualitätsskala (0–100) berücksichtigt Stärke des Entstehungsmusters, die Zahl unabhängiger Berührungen, Alter und bereits beobachtete Abweisungen. Sie ist eine frei gewählte technische Heuristik, **keine statistische Signifikanz oder Trefferwahrscheinlichkeit**.
- Eine Abweisung ist der größte Schlusskursabstand in günstiger Richtung von der jeweiligen Zonenkante während der Berührungs- und zwei Folgekerzen; nur vollständige Drei-Kerzen-Fenster zählen. Messung anhand der zur Entstehung eingefrorenen ATR.
- Für das Zusatzmerkmal „Abweisungen ≥ 0,5 ATR“ wird mindestens ein günstiger Schlusskursabstand von 0,5 Entstehungs-ATR verlangt. Eine bereits gebrochene Zone wird mit höchstens 20 Punkten bewertet.
- Diese aktuellen Rückblick-Merkmale sind **nicht historisch für einen Handelszeitpunkt rekonstruiert**. Vor dem Einsatz als Handelssignal wäre ein zeitpunktgetreuer Backtest erforderlich.
- Die Darstellung der Zeit kann zwischen UTC und MEZ/MESZ umgeschaltet werden. Der Chart bleibt intern in UTC geordnet (auch beim Wechsel in die Winterzeit); Tabellen und Tooltip verwenden die ausgewählte Zeitzone. Original-CSV und gespeicherte Quellkerzen bleiben UTC.
''')
        st.markdown('''**Indikatorkontext (separat von Strukturpunkten)**
- EMA 10/20/50/200: rekursiv mit α = 2/(Periode+1), Start beim ersten geladenen Schluss. Anzeige erst nach mindestens einer Periodenlänge; keine Rückwärtsauffüllung. EMA 200 bei weniger als 600 Kerzen als begrenzter Vorlauf gekennzeichnet.
- Stochastik: 100 × (Schluss − tiefstes Tief)/(höchstes Hoch − tiefstes Tief) im gewählten Lookback; K und D jeweils 3-fach einfach geglättet. Bei Nullspanne fehlt der Wert. Kreuzung nur zwischen den letzten zwei abgeschlossenen Kerzen.
- ATR bleibt der einfache Mittelwert der True Range über 14 Kerzen; kein Wilder-ATR. Zonenbreite_ATR = Breite / aktueller ATR der Zonen-Zeitebene.
- EMA-Abstand ist der kürzeste Abstand zur Zonengrenze, innerhalb der Zone null. EMA und ATR stammen aus derselben Zeitebene wie die Einzelzone. Nähe ist über die frei einstellbare ATR-Schwelle definiert.
- Kontext ist eine aktuelle Momentaufnahme, keine Rekonstruktion zum Entstehungszeitpunkt. Zeitebenen können unterschiedliche letzte Schlusszeiten haben. Gebrochene Zonen erhalten keinen aktiven EMA-Zonenkontext.
- EMA-Übereinstimmungen werden nicht zu Punkten oder Wahrscheinlichkeiten addiert. Mehrere EMAs beruhen auf denselben Kursdaten; ihre Nähe ist keine unabhängige Bestätigung.
''')
        st.markdown('''**Zusätzliche Sweep-Heuristik (0.4.1)**
- Nur nach bestätigter Zonenentstehung: Docht mindestens 0,05 der bei Zonenentstehung fixierten ATR jenseits der ungünstigen Zone.
- Reclaim: spätestens innerhalb von 3 weiteren Kerzen Schluss oberhalb der Obergrenze (Support) bzw. unterhalb der Untergrenze (Resistance).
- Folgeimpuls: innerhalb von 5 weiteren Kerzen Schluss über dem höheren Wert aus Obergrenze und vorherigem 3-Kerzen-Hoch plus 0,1 ATR (Long); für Short spiegelbildlich.
- Bei zwei aufeinanderfolgenden Schlüssen mindestens 0,1 ATR jenseits der ungünstigen Zonengrenze wird die Zone endgültig ausgeschlossen; historisch vorherige Sweeps bleiben dokumentiert.
- Ereigniszeitstempel sind die Intervallschlüsse in UTC. Sweep-Daten haben keine eigene, statistisch kalibrierte Trefferwahrscheinlichkeit.
- Die Marker im Chart kennzeichnen die Reclaim-Kerze; keine Aussage über tatsächliche Stop-Order-Liquidität.
''')
        st.markdown('''**Basis-Erkennungsregeln (seit 0.4.1 unverändert)**
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
