import hmac
import time
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from engine import TF, analyze, demo_data
from etoro import Client, FeedError
from zone_view import candle_table, bundle_zones, nearest_balanced, price_text

st.set_page_config(page_title='Zonenradar · eToro',page_icon='◈',layout='wide')
st.markdown('''<style>
.stApp {background: #0b1120;} [data-testid="stMetric"] {background:#142033;padding:16px;border-radius:12px;}
h1 {letter-spacing:-1px;} .block-container {padding-top:2rem;}
</style>''',unsafe_allow_html=True)
st.title('Zonenradar')
st.caption('eToro · 4H / 1H / 15Min / 5Min · Ansicht 0.2.0')

def secret(name):
    try:
        return str(st.secrets.get(name,''))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return ''

with st.sidebar:
    st.header('Arbeitsbereich')
    mode = st.radio('Datenquelle',['Demo · synthetische Daten','eToro · API'])
    count = st.select_slider('Kerzen je Zeitebene',options=[200,400,600,1000],value=400)
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
        status.append({'Zeitebene':tf,'Kerzen':len(d),'Beginn_UTC':d.time.iloc[0],'Letzter_Schluss_UTC':d.time.iloc[-1]+pd.Timedelta(minutes=TF[tf][1]),'Zeitlücken':gaps})
    all_z = pd.concat(tables,ignore_index=True) if tables else pd.DataFrame()
    cols = st.columns(3)
    cols[0].metric('Letzter 5Min-Schluss',f'{price:,.5f}')
    cols[1].metric('Zeitebenen','4')
    cols[2].metric('Datenstatus','Demo' if client is None else ('Historisch' if stale else 'Frisch nach Zeitprüfung'))
    st.caption(f'Kursstand: {last_close_time.tz_convert("Europe/Berlin"):%d.%m.%Y %H:%M} · kein ausführbarer Bid-/Ask-Kurs')
    if all_z.empty:
        st.info('Keine Zone nach den definierten Regeln gefunden.')
        all_z = pd.DataFrame(columns=['Typ','Seite','Untergrenze','Obergrenze','Status','Strukturpunkte','Tests','Abstand_ATR','Bekannt_ab','Letztes_Ereignis','Ereigniszeit','Begründung','Zeitebene'])
    all_z['Abstand_Preis'] = [max(r.Untergrenze-price,price-r.Obergrenze,0) for r in all_z.itertuples()]
    active = all_z[all_z.Status != 'Gebrochen'].copy()
    all_z['Überlappung_TF'] = [', '.join(tf for tf in TF if tf!=r.Zeitebene and ((active.Zeitebene==tf)&(active.Seite==r.Seite)&(active.Untergrenze<=r.Obergrenze)&(active.Obergrenze>=r.Untergrenze)).any()) for r in all_z.itertuples()]
    overview, charttab, zonetab, data_tab = st.tabs(['Überblick','Chart','Alle Zonen','Daten & Methode'])
    with overview:
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
                with st.expander('Herkunft und Einzelbewertungen'):
                    st.dataframe(pd.DataFrame(r.Details)[['Zeitebene','Typ','Untergrenze','Obergrenze','Strukturpunkte','Tests','Bekannt_ab','Begründung']],hide_index=True,width='stretch')
        st.markdown('**Jüngste beobachtete Ereignisse**')
        events = all_z[all_z.Ereigniszeit.notna()].sort_values('Ereigniszeit',ascending=False).head(10)
        st.dataframe(events[['Zeitebene','Typ','Letztes_Ereignis','Ereigniszeit','Status']],hide_index=True,width='stretch')
        st.caption('Ereigniszeiten in UTC. Historische Beobachtungen, keine Kauf-/Verkaufsfreigaben.')
    with charttab:
        tf = st.selectbox('Chart-Zeitebene',list(TF),index=2)
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
        chart_price = float(d.close.iloc[-1])
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
        fig = go.Figure(go.Candlestick(x=d.time,open=d.open,high=d.high,low=d.low,close=d.close,name=label,increasing_line_color='#36cbb0',decreasing_line_color='#f67b88'))
        for _,r in visible.iterrows():
            color = '#36cbb0' if r.Seite=='Unterstützung' else '#f67b88'
            start = max(d.time.iloc[0],r.Bekannt_ab)
            fig.add_shape(type='rect',x0=start,x1=end,y0=r.Untergrenze,y1=r.Obergrenze,line=dict(color=color,width=1),fillcolor=color,opacity=.16)
            fig.add_annotation(x=end,y=(r.Untergrenze+r.Obergrenze)/2,text=f"{r.Zone} {price_text(r.Untergrenze)}–{price_text(r.Obergrenze)}",showarrow=False,xanchor='right',font=dict(size=10,color=color),bgcolor='#0b1120')
        fig.update_layout(height=540,template='plotly_dark',paper_bgcolor='#0b1120',plot_bgcolor='#0b1120',xaxis_rangeslider_visible=False,margin=dict(l=5,r=5,t=15,b=10),yaxis_title='Kurs',xaxis_title='UTC',dragmode='pan')
        st.plotly_chart(fig,width='stretch',config={'displaylogo':False,'scrollZoom':False,'modeBarButtonsToRemove':['lasso2d','select2d']})
        st.caption('Grün/Rot = ursprüngliche Unterstützungs-/Widerstandsseite. Referenz: letzter Schluss der gewählten Chart-Zeitebene. Kein Einstiegssignal.')
        if not visible.empty:
            st.dataframe(visible[['Zone','Untergrenze','Obergrenze','Seite','Zeitebenen','Typen','Einzelzonen']],hide_index=True,width='stretch')
        st.caption('Bündel umfassen nur Bereiche derselben Seite mit gemeinsamer Preisüberschneidung. Ihre volle Breite wird erst ab der spätesten Bestätigung gezeichnet. Darstellung des heutigen Zustands, kein historischer Replay.')
    with zonetab:
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
        st.markdown('**Feed-Zeiträume**')
        st.dataframe(pd.DataFrame(status),hide_index=True,width='stretch')
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
