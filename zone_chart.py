"""Chart-only presentation and descriptive Fibonacci anchors."""
import math
import pandas as pd
import plotly.graph_objects as go
from zone_view import bundle_zones

ZONE_COLORS = {'Unterstützung/Widerstand':'#60a5fa', 'Angebot/Nachfrage':'#fbbf24', 'Liquidität vermutet':'#e879f9'}
FIB_LEVELS = (0, .236, .382, .5, .618, .786, 1)

def typed_groups(zones, price, bundled=True):
    parts = []
    if bundled:
        for kind in ZONE_COLORS:
            part = bundle_zones(zones[zones.Typ == kind], price)
            if not part.empty:
                parts.append(part)
    else:
        parts = [bundle_zones(pd.DataFrame([r]), price) for _, r in zones.iterrows()]
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

def fib_prices(start, end):
    if not all(math.isfinite(v) for v in [start,end]) or start == end:
        raise ValueError('Fibonacci benötigt zwei verschiedene, endliche Ankerpreise.')
    return [(r, end-r*(end-start)) for r in FIB_LEVELS]

def decorate(fig, visible, start, end, fib=None):
    for kind, color in ZONE_COLORS.items():
        if not visible.empty and (visible.Typen == kind).any():
            fig.add_trace(go.Scatter(x=[None], y=[None], mode='lines', name={'Unterstützung/Widerstand':'U/W','Angebot/Nachfrage':'Angebot/Nachfrage','Liquidität vermutet':'Liquidität'}[kind],
                                    line=dict(color=color,width=5)),row=1,col=1)
    for _, r in visible.iterrows():
        color = ZONE_COLORS[r.Typen]
        fig.add_shape(type='rect', x0=max(start,r.Bekannt_ab),x1=end,
                      y0=r.Untergrenze,y1=r.Obergrenze,
                      line=dict(color=color,width=1.5,dash='solid' if r.Seite=='Unterstützung' else 'dash'),
                      fillcolor=color,opacity=.22,row=1,col=1)
    if fib is not None:
        for ratio, value in fib_prices(*fib):
            fig.add_hline(y=value,line_color='#cbd5e1',line_dash='dot',line_width=1,
                          annotation_text=f'{ratio*100:g}% · {value:.5f}',
                          annotation_position='top left',annotation_font_size=10,row=1,col=1)
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
