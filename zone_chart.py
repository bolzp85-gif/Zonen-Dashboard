"""Chart-only presentation and descriptive Fibonacci anchors."""
import math
import pandas as pd
from engine import atr
import plotly.graph_objects as go
from zone_view import bundle_zones
from zone_time import TIMEZONES, format_time

ZONE_COLORS = {'Unterstützung':'#34d399','Widerstand':'#fb7185'}
FIB_LEVELS = (0.0, .382, .5, .618, .786, 1.0)  # Sechs Level, einschließlich 78,6 % und der beiden Anker

def typed_groups(zones, price, bundled=True, include_broken=False):
    parts = []
    if bundled:
        for kind in ZONE_COLORS:
            part = bundle_zones(zones[zones.Typ == kind], price, include_broken=include_broken)
            if not part.empty:
                parts.append(part)
    else:
        parts = [bundle_zones(pd.DataFrame([r]), price, include_broken=include_broken) for _, r in zones.iterrows()]
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True).sort_values('Abstand_Preis', kind='stable').reset_index(drop=True)
    out['Zone'] = [f'Z{i+1}' for i in range(len(out))]
    return out

def confirmed_swing(d, swing=3):
    """Latest opposite, unique pivots, each confirmed by three CLOSED bars."""
    pivots = []
    for p in range(swing, len(d)-swing):
        w = d.iloc[p-swing:p+swing+1]
        found = []
        for side, col, fn in [('low','low','min'),('high','high','max')]:
            value = float(d[col].iloc[p])
            if value == getattr(w[col], fn)() and (w[col] == value).sum() == 1:
                found.append((side, value, d.time.iloc[p]))
        if len(found) != 1:  # order within an outside bar is unknown
            continue
        pivot = found[0]
        if pivots and pivots[-1][0] == pivot[0]:
            pivots[-1] = pivot
        else:
            pivots.append(pivot)
    if len(pivots) < 2 or pivots[-2][1] == pivots[-1][1]:
        return None
    return pivots[-2], pivots[-1]

def significant_fib_swing(d, swing=3, min_atr_multiple=2.0, min_pct=0.25, max_age_bars=120):
    """Letztes relevantes *bestätigtes* gegensätzliches Swing-Paar.

    Schwünge werden aus vollständigen OHLC-Kerzen gebildet. Ein Pivot ist erst
    `swing` Kerzen nach dem eigentlichen Extrem bekannt. Für die Mindeststrecke
    wird die ATR(14) am Bestätigungszeitpunkt des zweiten Pivots verwendet;
    künftige ATR-Werte werden nicht benötigt. min_pct in Prozentpunkten (0.25=0,25%).

    Beide Mindestkriterien gelten (Maximum der zwei Schwellen). Wenn der letzte
    Schwung zu klein ist, kann ein älterer relevanter Schwung gewählt werden,
    sofern dessen Endpunkt nicht älter als max_age_bars ist.
    """
    if swing < 1 or not (math.isfinite(min_atr_multiple) and min_atr_multiple > 0):
        raise ValueError('Ungültige Fib-Swing-Parameter.')
    if not math.isfinite(min_pct) or min_pct < 0 or max_age_bars < 1:
        raise ValueError('Ungültige Fib-Swing-Parameter.')
    if len(d) < max(2*swing+1, 18):
        return None
    current_atr = atr(d, length=14)
    points = []
    for p in range(swing, len(d)-swing):
        window = d.iloc[p-swing:p+swing+1]
        found = []
        for kind, name, extremum in [('low','low','min'), ('high','high','max')]:
            price = float(d[name].iloc[p])
            values = window[name]
            if math.isfinite(price) and price == float(getattr(values,extremum)()) and (values==price).sum()==1:
                found.append((kind, price, d.time.iloc[p], p))
        if len(found) != 1:
            continue  # Outside-Kerze: zeitliche Reihenfolge von High und Low ist unbekannt
        point=found[0]
        if points and point[0]==points[-1][0]:
            # Bei mehreren aufeinanderfolgenden Hochs/Tiefs das prägnanteste
            # Extrem erhalten, nicht einfach das zuletzt registrierte.
            previous=points[-1]
            more_extreme=(point[1]>previous[1]) if point[0]=='high' else (point[1]<previous[1])
            if more_extreme:
                points[-1]=point
        else:
            points.append(point)
    if len(points)<2:
        return None
    for first, last in zip(points[-2::-1], points[:0:-1]):
        # Zweites Extrem ist der jüngere Anker; Bestätigung + swing Kerzen.
        if len(d)-1-last[3] > max_age_bars:
            break
        known=last[3]+swing
        scale=float(current_atr.iloc[known])
        if not math.isfinite(scale) or scale <= 0:
            continue
        threshold=max(min_atr_multiple*scale, abs(last[1])*min_pct/100.0)
        if abs(last[1]-first[1]) >= threshold:
            return first[:3], last[:3]
    return None


def fib_prices(start, end):
    if not all(math.isfinite(v) for v in [start,end]) or start == end:
        raise ValueError('Fibonacci benötigt zwei verschiedene, endliche Ankerpreise.')
    return [(r, end-r*(end-start)) for r in FIB_LEVELS]

def _format_fib_price(value):
    """Kompakte, assettaugliche Preisangabe (Indizes, Öl, Forex)."""
    size=abs(value)
    places = 2 if size >= 1000 else 3 if size >= 10 else 5
    # Dezimal-Komma für deutsche Chartbeschriftung, keine Tausender-Abtrennung.
    return f'{value:.{places}f}'.replace('.', ',')


def decorate(fig, visible, start, end, fib=None):
    for kind, color in ZONE_COLORS.items():
        if not visible.empty and (visible.Typen == kind).any():
            fig.add_trace(go.Scatter(x=[None], y=[None], mode='lines', name=kind,
                                    line=dict(color=color,width=5)),row=1,col=1)
    for _, r in visible.iterrows():
        color = "#94a3b8" if r.Status == "Gebrochen" else ZONE_COLORS[r.Typen]
        fig.add_shape(type='rect', x0=max(start,r.Bekannt_ab),x1=end,
                      y0=r.Untergrenze,y1=r.Obergrenze,
                      line=dict(color=color,width=1.5,dash='dot' if r.Status=='Gebrochen' else 'solid' if r.Seite=='Unterstützung' else 'dash'),
                      fillcolor=color,opacity=.12 if r.Status == 'Gebrochen' else .22,row=1,col=1)
    if fib is not None:
        # Alle sechs Fibonacci-Level liegen auf den ECHTEN Preisniveaus.
        # Rechts an jeder Linie ein eigenes Label im KURSPLOT (yref='y'),
        # nicht in Papierkoordinaten oberhalb des Charts wie bis v0.5.4.
        # Null bezeichnet den letzten Swing-Anker, 100 den ersten Anker.
        for ratio, value in fib_prices(*fib):
            outer = ratio in (0.0, 1.0)
            color = '#D7B77C' if outer else '#CBD5E1'
            fig.add_hline(y=value, line_color=color, line_dash='dot',
                          line_width=1.3 if outer else 1,
                          opacity=0.85 if outer else 0.68, layer='below', row=1, col=1)
            # Die Beschriftung liegt innerhalb der rechten Chartkante und ist
            # exakt am Kursniveau verankert. Das dunkle Feld verhindert, dass
            # weiße Texte mit Linien/Kerzen visuell verschmelzen.
            label = f'{ratio*100:.1f}'.replace('.', ',') + '%'
            fig.add_annotation(
                x=0.995, xref='paper', y=value, yref='y',
                text=f'<b>{label}</b>  {_format_fib_price(value)}',
                showarrow=False, xanchor='right', yanchor='middle',
                align='right', font=dict(size=10, color=color),
                bgcolor='rgba(11,17,32,0.91)',
                bordercolor='rgba(148,163,184,0.22)', borderwidth=0.5,
                borderpad=2)
    # Legende bleibt oberhalb des Plotbereichs, Fibonacci-Texte ausschließlich
    # innerhalb des Charts; keine zusätzliche Header-Höhe für Fib notwendig.
    fig.update_layout(legend=dict(orientation='h',yanchor='bottom',y=1.02,x=0,xanchor='left',font=dict(size=10)),
                      margin=dict(l=3,r=3,t=110,b=25),hovermode='x unified')
    fig.update_yaxes(automargin=True,side='right',title_text=None)
    return fig

def fullscreen_html(fig):
    """Standalone responsive document, no CDN or API credentials."""
    import plotly.io as pio
    copy = go.Figure(fig)
    copy.update_layout(height=None,autosize=True)
    html = pio.to_html(copy,include_plotlyjs=True,full_html=True,default_width='100%',default_height='100%',
                       config={'responsive':True,'displaylogo':False,'scrollZoom':False})
    return html.replace('<head>', '<head><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><style>html,body{margin:0;background:#0b1120;width:100%;height:100%;overflow:hidden}body>div{width:100vw!important;height:100dvh!important}</style>')


def decorate_sweeps(fig, events, start, end, minutes, max_markers=40, time_choice=TIMEZONES[0]):
    """Mark reclaim candles, not presumed resting liquidity or trade entries.

    Event timestamps are candle close UTC; Plotly candles are indexed by open.
    """
    if events.empty:
        return fig
    one_bar = pd.Timedelta(minutes=minutes)
    selected = events[(events.Rueckeroberung_UTC > start) &
                      (events.Rueckeroberung_UTC <= end)].sort_values('Rueckeroberung_UTC').tail(max_markers)
    selected = selected.copy()
    selected['Sweep_Zeitansicht'] = selected.Sweep_UTC.map(lambda t: format_time(t, time_choice))
    selected['Rueck_Zeitansicht'] = selected.Rueckeroberung_UTC.map(lambda t: format_time(t, time_choice))
    for side, symbol, color, name in [
            ('Long-Reaktion','triangle-up','#34d399','Sweep-Reclaim Long'),
            ('Short-Reaktion','triangle-down','#fb7185','Sweep-Reclaim Short')]:
        part = selected[selected.Seite == side]
        if part.empty:
            continue
        fig.add_trace(go.Scatter(
            x=part.Rueckeroberung_UTC-one_bar,
            y=part.Rueckeroberung_Preis,
            mode='markers', name=name, showlegend=False,
            marker=dict(symbol=symbol,size=12,color=color,line=dict(color='#0b1120',width=1.5)),
            customdata=part[['Typ','Status','Durchbruch_ATR','Sweep_Zeitansicht','Rueck_Zeitansicht']].astype(str).to_numpy(),
            hovertemplate='%{fullData.name}<br>Rückeroberung: %{customdata[4]}<br>Kurs: %{y:.5f}<br>'
                          'Typ: %{customdata[0]}<br>Status: %{customdata[1]}<br>'
                          'Sweep-Tiefe (ATR): %{customdata[2]}<br>'
                          'Sweep-Schluss: %{customdata[3]}<extra></extra>'
        ),row=1,col=1)
    return fig
