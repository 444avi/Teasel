"""Pure options payoff-at-expiration engine.

Standard library only. Prices are Decimal dollars (at most 2 decimal places);
money is integer cents. The payoff is piecewise linear with kinks only at
strikes, so it is evaluated exactly at breakpoints, never sampled.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN
from typing import Literal, Sequence

OptionKind = Literal["CALL", "PUT", "STOCK"]
Direction = Literal["LONG", "SHORT"]

CENT = Decimal("0.01")
BREAKEVEN_QUANTUM = Decimal("0.0001")


def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"not a valid number: {value!r}") from exc


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_cents_precision(value: Decimal, name: str) -> None:
    if not value.is_finite() or value != value.quantize(CENT):
        raise ValueError(f"{name} must have at most 2 decimal places")


@dataclass(frozen=True)
class OptionLeg:
    kind: OptionKind
    direction: Direction
    qty: int
    strike: Decimal | None
    premium_cents: int
    multiplier: int = 100
    commission_cents: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", str(self.kind).upper())
        object.__setattr__(self, "direction", str(self.direction).upper())
        if self.kind not in ("CALL", "PUT", "STOCK"):
            raise ValueError("kind must be CALL, PUT or STOCK")
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError("direction must be LONG or SHORT")
        if not _is_int(self.qty) or self.qty < 1:
            raise ValueError("qty must be a positive integer")
        if self.kind == "STOCK":
            if self.strike is not None:
                raise ValueError("a stock leg cannot have a strike")
        else:
            if self.strike is None:
                raise ValueError("an option leg requires a strike")
            strike = _decimal(self.strike)
            _check_cents_precision(strike, "strike")
            if strike <= 0:
                raise ValueError("strike must be greater than zero")
            object.__setattr__(self, "strike", strike)
        if not _is_int(self.premium_cents) or self.premium_cents < 0:
            raise ValueError("premium must be a non-negative integer number of cents")
        if not _is_int(self.multiplier) or self.multiplier < 1:
            raise ValueError("multiplier must be a positive integer")
        if not _is_int(self.commission_cents) or self.commission_cents < 0:
            raise ValueError("commission must be a non-negative integer number of cents")

    @property
    def effective_multiplier(self) -> int:
        return 1 if self.kind == "STOCK" else self.multiplier

    @property
    def sign(self) -> int:
        return 1 if self.direction == "LONG" else -1

    def value_cents(self, price: Decimal) -> int:
        """Per-share value at expiration, in whole cents."""
        if self.kind == "CALL":
            value = max(price - self.strike, Decimal(0))
        elif self.kind == "PUT":
            value = max(self.strike - price, Decimal(0))
        else:
            value = price
        scaled = value * 100
        cents = int(scaled)
        assert cents == scaled, "prices must be whole cents"
        return cents

    def pl_cents(self, price: Decimal) -> int:
        return self.sign * self.qty * self.effective_multiplier * (self.value_cents(price) - self.premium_cents)


@dataclass(frozen=True)
class Guard:
    code: str
    severity: Literal["warning", "info"]
    message: str
    leg_indexes: tuple[int, ...] = ()


@dataclass(frozen=True)
class OptionsResult:
    net_premium_cents: int
    commissions_cents: int
    max_profit_cents: int | None
    max_profit_unlimited: bool
    max_loss_cents: int | None
    max_loss_unlimited: bool
    breakevens: tuple[Decimal, ...]
    breakeven_ranges: tuple[tuple[Decimal, Decimal | None], ...]
    upside_slope_cents_per_dollar: int
    strikes: tuple[Decimal, ...]
    spot: Decimal | None
    pl_at_spot_cents: int | None
    price_range: tuple[Decimal, Decimal]
    vertices: tuple[tuple[Decimal, int], ...]
    guards: tuple[Guard, ...]
    leg_count: int

    def to_dict(self) -> dict:
        def normalize(value: object) -> object:
            if isinstance(value, Decimal):
                return float(value)
            if isinstance(value, tuple):
                return [normalize(item) for item in value]
            if isinstance(value, list):
                return [normalize(item) for item in value]
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            return value

        payload = asdict(self)
        return normalize(payload)  # type: ignore[return-value]


def _pl_cents(legs: Sequence[OptionLeg], price: Decimal, commissions: int) -> int:
    return sum(leg.pl_cents(price) for leg in legs) - commissions


def _validate_price(value: object, name: str) -> Decimal:
    price = _decimal(value)
    _check_cents_precision(price, name)
    return price


def _crossing(a: Decimal, b: Decimal, pa: int, pb: int) -> Decimal:
    x = a + (b - a) * Decimal(-pa) / Decimal(pb - pa)
    return x.quantize(BREAKEVEN_QUANTUM, rounding=ROUND_HALF_EVEN)


def _breakevens(
    points: Sequence[Decimal], values: Sequence[int], slope: int,
) -> tuple[tuple[Decimal, ...], tuple[tuple[Decimal, Decimal | None], ...]]:
    ranges: list[list[Decimal | None]] = []
    crossings: set[Decimal] = set()

    def add_range(start: Decimal, end: Decimal | None) -> None:
        if ranges and ranges[-1][1] == start:
            ranges[-1][1] = end
        else:
            ranges.append([start, end])

    for index in range(len(points) - 1):
        a, b = points[index], points[index + 1]
        pa, pb = values[index], values[index + 1]
        if pa == 0 and pb == 0:
            add_range(a, b)
        elif (pa < 0 < pb) or (pb < 0 < pa):
            crossings.add(_crossing(a, b, pa, pb))
    last, last_pl = points[-1], values[-1]
    if last_pl == 0 and slope == 0:
        add_range(last, None)
    elif last_pl != 0 and slope != 0 and (last_pl > 0) != (slope > 0):
        crossing = last + Decimal(-last_pl) / Decimal(slope)
        crossings.add(crossing.quantize(BREAKEVEN_QUANTUM, rounding=ROUND_HALF_EVEN))

    def in_range(price: Decimal) -> bool:
        return any(start <= price and (end is None or price <= end) for start, end in ranges)

    for price, value in zip(points, values):
        if value == 0 and not in_range(price):
            crossings.add(price.quantize(BREAKEVEN_QUANTUM))
    return (
        tuple(sorted(crossings)),
        tuple((start, end) for start, end in ranges),  # type: ignore[misc]
    )


def _guards(
    legs: Sequence[OptionLeg],
    spot: Decimal | None,
    slope: int,
    lowest: int,
    highest: int,
    profit_unlimited: bool,
    loss_unlimited: bool,
) -> tuple[Guard, ...]:
    guards: list[Guard] = []
    option_indexes = [index for index, leg in enumerate(legs) if leg.kind != "STOCK"]
    if slope < 0:
        guards.append(Guard(
            "UNLIMITED_LOSS", "warning",
            "Short calls are not fully covered. Loss is unlimited if the price rises.",
            tuple(index for index, leg in enumerate(legs) if leg.kind == "CALL" and leg.direction == "SHORT"),
        ))
    if not loss_unlimited and lowest > 0:
        guards.append(Guard(
            "GUARANTEED_PROFIT", "warning",
            "Every outcome is profitable. Check the premiums for a typo.",
        ))
    if not profit_unlimited and highest <= 0:
        guards.append(Guard("NO_PROFIT_POSSIBLE", "info", "No outcome at expiration is profitable."))
    if spot is not None:
        for index in option_indexes:
            if legs[index].premium_cents < legs[index].value_cents(spot):
                guards.append(Guard(
                    "PREMIUM_BELOW_INTRINSIC", "info",
                    f"Leg {index + 1} premium is below its intrinsic value at the entered spot.",
                    (index,),
                ))
    zero_premium = tuple(index for index in option_indexes if legs[index].premium_cents == 0)
    if zero_premium:
        guards.append(Guard(
            "ZERO_PREMIUM", "info",
            "Some option premiums are zero. Enter the prices you would trade at.",
            zero_premium,
        ))
    if len({legs[index].multiplier for index in option_indexes}) > 1:
        guards.append(Guard(
            "MIXED_MULTIPLIERS", "info", "Legs use different contract multipliers.",
            tuple(option_indexes),
        ))
    return tuple(guards)


def analyze_options(
    legs: Sequence[OptionLeg],
    spot: Decimal | None = None,
    price_range: tuple[Decimal, Decimal] | None = None,
) -> OptionsResult:
    legs = tuple(legs)
    if not legs:
        raise ValueError("at least one leg is required")
    if spot is not None:
        spot = _validate_price(spot, "spot")
        if spot <= 0:
            raise ValueError("spot must be greater than zero")

    commissions = sum(leg.commission_cents for leg in legs)
    net_premium = sum(-leg.sign * leg.qty * leg.effective_multiplier * leg.premium_cents for leg in legs)
    slope = sum(
        leg.sign * leg.qty * leg.effective_multiplier * 100
        for leg in legs if leg.kind in ("CALL", "STOCK")
    )
    strikes = tuple(sorted({leg.strike for leg in legs if leg.strike is not None}))
    points = tuple(sorted({Decimal(0), *strikes}))
    values = tuple(_pl_cents(legs, point, commissions) for point in points)

    highest, lowest = max(values), min(values)
    profit_unlimited = slope > 0
    loss_unlimited = slope < 0
    breakevens, breakeven_ranges = _breakevens(points, values, slope)

    if price_range is None:
        reference = set(strikes)
        reference.update(Decimal(leg.premium_cents) / 100 for leg in legs if leg.kind == "STOCK")
        if spot is not None:
            reference.add(spot)
        reference.update(breakevens)
        if not reference:
            reference.add(Decimal(0))
        lo, hi = min(reference), max(reference)
        pad = max((hi - lo) * Decimal("0.25"), hi * Decimal("0.10"))
        x_min = max(Decimal(0), lo - pad).quantize(CENT, rounding=ROUND_FLOOR)
        x_max = (hi + pad).quantize(CENT, rounding=ROUND_CEILING)
        if x_max <= x_min:
            x_max = x_min + 1
    else:
        x_min = _validate_price(price_range[0], "price range")
        x_max = _validate_price(price_range[1], "price range")
        if not Decimal(0) <= x_min < x_max:
            raise ValueError("price range must satisfy 0 <= min < max")

    vertex_prices = {x_min, x_max}
    vertex_prices.update(point for point in points if x_min < point < x_max)
    zero_prices = {price for price in breakevens if x_min < price < x_max}
    vertices = tuple(
        (price, 0 if price in zero_prices and price not in points else _pl_cents(legs, price, commissions))
        for price in sorted(vertex_prices | zero_prices)
    )

    return OptionsResult(
        net_premium_cents=net_premium,
        commissions_cents=commissions,
        max_profit_cents=None if profit_unlimited else highest,
        max_profit_unlimited=profit_unlimited,
        max_loss_cents=None if loss_unlimited else max(0, -lowest),
        max_loss_unlimited=loss_unlimited,
        breakevens=breakevens,
        breakeven_ranges=breakeven_ranges,
        upside_slope_cents_per_dollar=slope,
        strikes=strikes,
        spot=spot,
        pl_at_spot_cents=None if spot is None else _pl_cents(legs, spot, commissions),
        price_range=(x_min, x_max),
        vertices=vertices,
        guards=_guards(legs, spot, slope, lowest, highest, profit_unlimited, loss_unlimited),
        leg_count=len(legs),
    )
