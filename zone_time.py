"""Display conversions ONLY: computational and exported source timestamps remain UTC.

Europe/Berlin automatically observes Central European Standard/Summer Time.
"""
import numpy as np
import pandas as pd

TIMEZONES = ('UTC', 'MEZ/MESZ (Deutschland)')


def tz_name(choice):
    if choice == TIMEZONES[0]:
        return 'UTC'
    if choice == TIMEZONES[1]:
        return 'Europe/Berlin'
    raise ValueError('Nicht unterstützte Anzeige-Zeitzone.')


def label(choice):
    tz_name(choice)
    return 'UTC' if choice == TIMEZONES[0] else 'MEZ/MESZ'


def localized(ts, choice):
    if ts is None or pd.isna(ts):
        return pd.NaT
    stamp = pd.Timestamp(ts)
    stamp = stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')
    return stamp.tz_convert(tz_name(choice))


def format_time(ts, choice, compact=False):
    stamp = localized(ts, choice)
    if pd.isna(stamp):
        return '—'
    abbreviation = 'UTC' if choice == TIMEZONES[0] else ('MESZ' if stamp.utcoffset().total_seconds() == 7200 else 'MEZ')
    return stamp.strftime('%d.%m. %H:%M' if compact else '%d.%m.%Y %H:%M:%S') + f' {abbreviation}'


def display_table(frame, fields, choice):
    """Human-readable text, never passed to calculations or CSV snapshots."""
    result = frame.copy()
    tzlabel = label(choice)
    renamed = {}
    for col in fields:
        if col not in result.columns:
            continue
        result[col] = result[col].map(lambda value: format_time(value, choice))
        renamed[col] = (col[:-4] if col.endswith('_UTC') else col) + f' [{tzlabel}]'
    return result.rename(columns=renamed)


def axis_ticks(times, choice, maximum=8):
    """Real x-axis values stay UTC, so the DST autumn repeated hour is safe."""
    values = pd.to_datetime(pd.Series(times), utc=True).dropna().reset_index(drop=True)
    if values.empty:
        return [], []
    chosen = np.unique(np.linspace(0, len(values)-1, num=min(maximum,len(values)), dtype=int))
    selected = values.iloc[chosen]
    return list(selected), [format_time(t, choice, compact=True) for t in selected]
