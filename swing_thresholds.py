"""Shared, explicit (not empirically optimised) Fibonacci swing-size settings.

One volatility-relative and one price-relative floor must BOTH be met.
No feedback from the size of the same candidate swing is used to choose a floor.
"""
from __future__ import annotations
from dataclasses import dataclass
import math

# Educational default candidates; validate on independent histories before trading.
SWING_DEFAULTS = {
    '5Min': (1.5, .05),
    '15Min': (1.5, .10),
    '1H': (2.0, .20),
    '4H': (2.0, .30),
    '1D': (2.5, .50),
}

@dataclass(frozen=True)
class SwingThreshold:
    atr_multiple: float
    pct: float  # percent UNITS: .10 means 0.10%, not 10%.

    def __post_init__(self):
        if (not math.isfinite(self.atr_multiple) or self.atr_multiple <= 0 or
            not math.isfinite(self.pct) or self.pct < 0):
            raise ValueError('Ungültige Swing-Schwellen.')


def settings(tf: str, overrides: dict | None = None) -> SwingThreshold:
    if tf not in SWING_DEFAULTS:
        raise ValueError(f'Unbekannte Zeitebene: {tf}')
    if overrides and tf in overrides:
        value = overrides[tf]
        if isinstance(value, SwingThreshold):
            return value
        return SwingThreshold(float(value[0]), float(value[1]))
    return SwingThreshold(*SWING_DEFAULTS[tf])


def assess_swing(distance: float, *, price: float, atr: float,
                 threshold: SwingThreshold) -> tuple[bool, str, float]:
    """Returns accepted, specific reject reason, and required absolute distance."""
    vals = (distance, price, atr)
    if any(not math.isfinite(v) for v in vals) or distance < 0 or atr <= 0:
        return False, 'invalid_atr', float('nan')
    atr_floor = threshold.atr_multiple * atr
    pct_floor = abs(price) * threshold.pct / 100.0
    atr_reject = distance < atr_floor
    pct_reject = distance < pct_floor
    reason = ('both' if atr_reject and pct_reject else 'atr' if atr_reject
              else 'pct' if pct_reject else 'accepted')
    return reason == 'accepted', reason, max(atr_floor, pct_floor)
