"""Named options strategies built around a spot price and strike width.

Standard library only. Option premiums start at zero so the ZERO_PREMIUM guard
prompts the user to enter the prices they would actually trade at.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from teasel.options import CENT, OptionLeg, _decimal

MAX_PRESET_QTY = 10_000


@dataclass(frozen=True)
class PresetLeg:
    direction: str
    kind: str
    offset: Optional[int]  # strike steps from ATM; None for STOCK
    qty_factor: int


PRESET_LABELS: dict[str, str] = {
    "long_call": "Long call",
    "long_put": "Long put",
    "covered_call": "Covered call",
    "protective_put": "Protective put",
    "bull_call_spread": "Bull call spread",
    "bear_put_spread": "Bear put spread",
    "long_straddle": "Long straddle",
    "long_strangle": "Long strangle",
    "iron_condor": "Iron condor",
    "call_butterfly": "Long call butterfly",
}

PRESETS: dict[str, tuple[PresetLeg, ...]] = {
    "long_call": (PresetLeg("LONG", "CALL", 0, 1),),
    "long_put": (PresetLeg("LONG", "PUT", 0, 1),),
    "covered_call": (PresetLeg("LONG", "STOCK", None, 100), PresetLeg("SHORT", "CALL", 1, 1)),
    "protective_put": (PresetLeg("LONG", "STOCK", None, 100), PresetLeg("LONG", "PUT", -1, 1)),
    "bull_call_spread": (PresetLeg("LONG", "CALL", 0, 1), PresetLeg("SHORT", "CALL", 1, 1)),
    "bear_put_spread": (PresetLeg("LONG", "PUT", 0, 1), PresetLeg("SHORT", "PUT", -1, 1)),
    "long_straddle": (PresetLeg("LONG", "CALL", 0, 1), PresetLeg("LONG", "PUT", 0, 1)),
    "long_strangle": (PresetLeg("LONG", "PUT", -1, 1), PresetLeg("LONG", "CALL", 1, 1)),
    "iron_condor": (
        PresetLeg("LONG", "PUT", -2, 1),
        PresetLeg("SHORT", "PUT", -1, 1),
        PresetLeg("SHORT", "CALL", 1, 1),
        PresetLeg("LONG", "CALL", 2, 1),
    ),
    "call_butterfly": (
        PresetLeg("LONG", "CALL", -1, 1),
        PresetLeg("SHORT", "CALL", 0, 2),
        PresetLeg("LONG", "CALL", 1, 1),
    ),
}


def _positive_price(value: object, name: str) -> Decimal:
    price = _decimal(value)
    if not price.is_finite() or price != price.quantize(CENT):
        raise ValueError(f"{name} must have at most 2 decimal places")
    if price <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return price


def preset_catalog() -> list[dict]:
    return [{"name": name, "label": PRESET_LABELS[name]} for name in PRESETS]


def build_preset(name: str, spot: Decimal, width: Decimal, qty: int = 1) -> list[OptionLeg]:
    if name not in PRESETS:
        raise ValueError(f"unknown preset: {name}")
    spot = _positive_price(spot, "spot")
    width = _positive_price(width, "width")
    if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= MAX_PRESET_QTY:
        raise ValueError(f"qty must be an integer from 1 to {MAX_PRESET_QTY}")
    atm = (spot / width).quantize(Decimal(1), rounding=ROUND_HALF_UP) * width
    legs: list[OptionLeg] = []
    for template in PRESETS[name]:
        if template.kind == "STOCK":
            legs.append(OptionLeg("STOCK", template.direction, qty * template.qty_factor, None, int(spot * 100)))
            continue
        strike = atm + template.offset * width
        if strike <= 0:
            raise ValueError("preset strikes must be greater than zero; raise the spot or narrow the width")
        legs.append(OptionLeg(template.kind, template.direction, qty * template.qty_factor, strike, 0))
    return legs
