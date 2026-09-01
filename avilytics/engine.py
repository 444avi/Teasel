"""Pure scalar-ladder payoff and diagnostic engine.

The module deliberately depends only on the Python standard library. Money is
represented as integer cents; probabilities and thresholds use Decimal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Iterable, Literal, Sequence

Side = Literal["YES", "NO"]


def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"not a valid number: {value!r}") from exc


def _display_number(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


@dataclass(frozen=True)
class OutcomeBin:
    index: int
    lower: Decimal | None
    upper: Decimal | None
    label: str


@dataclass(frozen=True)
class Leg:
    market_id: str
    threshold: Decimal
    side: Side
    qty: int
    entry_price_cents: int
    fee_cents: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "threshold", _decimal(self.threshold))
        object.__setattr__(self, "side", str(self.side).upper())
        if self.side not in ("YES", "NO"):
            raise ValueError("side must be YES or NO")
        if not isinstance(self.qty, int) or self.qty < 0:
            raise ValueError("qty must be a non-negative integer")
        if not isinstance(self.entry_price_cents, int) or not 0 <= self.entry_price_cents <= 100:
            raise ValueError("entry price must be an integer from 0 to 100 cents")
        if not isinstance(self.fee_cents, int) or self.fee_cents < 0:
            raise ValueError("fee must be a non-negative integer number of cents")

    @property
    def cost_cents(self) -> int:
        return self.entry_price_cents * self.qty + self.fee_cents


@dataclass(frozen=True)
class FeeModel:
    rate: Decimal = Decimal("0.07")
    use_maker: bool = False

    def fee_cents(self, price_cents: int, qty: int) -> int:
        """Kalshi taker fee, rounded up to the next whole cent per leg."""
        if self.use_maker or qty == 0:
            return 0
        if not 0 <= price_cents <= 100 or qty < 0:
            raise ValueError("invalid price or quantity")
        price = Decimal(price_cents) / Decimal(100)
        fee_in_cents = self.rate * Decimal(qty) * price * (Decimal(1) - price) * Decimal(100)
        return int(fee_in_cents.to_integral_value(rounding=ROUND_CEILING))


@dataclass(frozen=True)
class MarketProbabilities:
    valid: bool
    bin_probabilities: tuple[Decimal, ...] | None
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class BinPayoff:
    bin: OutcomeBin
    gross_cents: int
    net_cents: int
    probability: Decimal | None


@dataclass(frozen=True)
class AnalysisResult:
    thresholds: tuple[Decimal, ...]
    bins: tuple[BinPayoff, ...]
    total_entry_cents: int
    total_fees_cents: int
    total_cost_cents: int
    min_gross_cents: int
    max_loss_cents: int
    max_gain_cents: int
    breakeven_bin_indexes: tuple[int, ...]
    profitable_bin_indexes: tuple[int, ...]
    profit_probability: Decimal | None
    market_ev_cents: Decimal | None
    monotonicity_ok: bool
    guaranteed_arbitrage: bool
    arbitrage_alarm: bool
    guard_messages: tuple[str, ...]

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


def normalize_thresholds(thresholds: Iterable[object]) -> tuple[Decimal, ...]:
    values = tuple(sorted(_decimal(value) for value in thresholds))
    if not values:
        raise ValueError("at least one threshold is required")
    if len(set(values)) != len(values):
        raise ValueError("thresholds must be unique")
    return values


def build_bins(thresholds: Iterable[object]) -> tuple[OutcomeBin, ...]:
    values = normalize_thresholds(thresholds)
    bins: list[OutcomeBin] = []
    for index in range(len(values) + 1):
        lower = values[index - 1] if index > 0 else None
        upper = values[index] if index < len(values) else None
        if lower is None:
            label = f"≤ {_display_number(upper)}"
        elif upper is None:
            label = f"≥ {_display_number(lower)}"
        else:
            label = f"({_display_number(lower)}, {_display_number(upper)}]"
        bins.append(OutcomeBin(index=index, lower=lower, upper=upper, label=label))
    return tuple(bins)


def payoff_matrix(thresholds: Iterable[object], legs: Sequence[Leg]) -> tuple[tuple[int, ...], ...]:
    values = normalize_thresholds(thresholds)
    threshold_indexes = {threshold: index for index, threshold in enumerate(values)}
    matrix: list[tuple[int, ...]] = []
    for bin_index in range(len(values) + 1):
        row: list[int] = []
        for leg in legs:
            if leg.threshold not in threshold_indexes:
                raise ValueError(f"leg threshold {leg.threshold} is not present in the ladder")
            rung_index = threshold_indexes[leg.threshold]
            row.append(int(bin_index > rung_index) if leg.side == "YES" else int(bin_index <= rung_index))
        matrix.append(tuple(row))
    return tuple(matrix)


def derive_bin_probabilities(p_above: Sequence[object]) -> MarketProbabilities:
    """Convert cumulative P(outcome > threshold) values into exclusive bins."""
    cumulative = tuple(_decimal(value) for value in p_above)
    errors: list[str] = []
    for index, probability in enumerate(cumulative):
        if not Decimal(0) <= probability <= Decimal(1):
            errors.append(f"P(above) at rung {index + 1} is outside [0, 1]")
    for index in range(len(cumulative) - 1):
        if cumulative[index + 1] > cumulative[index]:
            errors.append(
                f"P(above) increases from rung {index + 1} to {index + 2}; quotes are stale or incoherent"
            )
    if errors:
        return MarketProbabilities(False, None, tuple(errors))
    if not cumulative:
        return MarketProbabilities(False, None, ("at least one cumulative probability is required",))
    probabilities = [Decimal(1) - cumulative[0]]
    probabilities.extend(cumulative[index] - cumulative[index + 1] for index in range(len(cumulative) - 1))
    probabilities.append(cumulative[-1])
    return MarketProbabilities(True, tuple(probabilities))


def derive_cumulative_probabilities(bin_probabilities: Sequence[object]) -> tuple[Decimal, ...]:
    """Convert exclusive low-to-high bin probabilities to cumulative Above rungs."""
    probabilities = tuple(_decimal(value) for value in bin_probabilities)
    if len(probabilities) < 2:
        raise ValueError("at least two exclusive bins are required")
    if any(value < 0 or value > 1 for value in probabilities):
        raise ValueError("bin probabilities must be within [0, 1]")
    if sum(probabilities) != Decimal(1):
        raise ValueError("bin probabilities must sum to 1")
    return tuple(sum(probabilities[index + 1 :]) for index in range(len(probabilities) - 1))


def analyze_position(
    thresholds: Iterable[object],
    legs: Sequence[Leg],
    p_above: Sequence[object] | None = None,
) -> AnalysisResult:
    values = normalize_thresholds(thresholds)
    outcome_bins = build_bins(values)
    matrix = payoff_matrix(values, legs)
    total_entry = sum(leg.entry_price_cents * leg.qty for leg in legs)
    total_fees = sum(leg.fee_cents for leg in legs)
    total_cost = total_entry + total_fees
    gross = tuple(
        sum(cell * leg.qty * 100 for cell, leg in zip(row, legs))
        for row in matrix
    )
    net = tuple(value - total_cost for value in gross)

    probabilities: tuple[Decimal, ...] | None = None
    monotonicity_ok = True
    guard_messages: list[str] = []
    if p_above is not None:
        probability_result = derive_bin_probabilities(p_above)
        monotonicity_ok = probability_result.valid
        probabilities = probability_result.bin_probabilities
        guard_messages.extend(probability_result.errors)

    min_gross = min(gross)
    max_loss = max(0, total_cost - min_gross)
    max_gain = max(net)
    guaranteed_arbitrage = min_gross > total_cost
    if guaranteed_arbitrage:
        guard_messages.append("Guaranteed minimum payout exceeds total cost.")

    market_ev: Decimal | None = None
    profit_probability: Decimal | None = None
    if probabilities is not None:
        market_ev = sum(probability * Decimal(payoff) for probability, payoff in zip(probabilities, net))
        profit_probability = sum(
            probability for probability, payoff in zip(probabilities, net) if payoff > 0
        )
    ev_arbitrage = market_ev is not None and market_ev > 0
    if ev_arbitrage:
        guard_messages.append("Positive market-implied EV is an arbitrage/coherence alarm, not a buy signal.")

    bin_payoffs = tuple(
        BinPayoff(
            bin=outcome_bin,
            gross_cents=gross[index],
            net_cents=net[index],
            probability=probabilities[index] if probabilities is not None else None,
        )
        for index, outcome_bin in enumerate(outcome_bins)
    )
    return AnalysisResult(
        thresholds=values,
        bins=bin_payoffs,
        total_entry_cents=total_entry,
        total_fees_cents=total_fees,
        total_cost_cents=total_cost,
        min_gross_cents=min_gross,
        max_loss_cents=max_loss,
        max_gain_cents=max_gain,
        breakeven_bin_indexes=tuple(index for index, payoff in enumerate(net) if payoff == 0),
        profitable_bin_indexes=tuple(index for index, payoff in enumerate(net) if payoff > 0),
        profit_probability=profit_probability,
        market_ev_cents=market_ev,
        monotonicity_ok=monotonicity_ok,
        guaranteed_arbitrage=guaranteed_arbitrage,
        arbitrage_alarm=guaranteed_arbitrage or bool(ev_arbitrage),
        guard_messages=tuple(guard_messages),
    )
