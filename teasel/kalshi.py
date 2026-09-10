"""Read-only Kalshi ingestion adapter for Teasel."""

from __future__ import annotations

import json
import random
import re
import socket
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = "Teasel/1.0 (read-only scalar ladder analyzer)"


class KalshiIngestError(RuntimeError):
    pass


def parse_event_ticker(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    if "/" in value or "://" in value:
        parsed = urlparse(value if "://" in value else "https://" + value)
        segments = [segment for segment in parsed.path.split("/") if segment]
        if segments:
            value = segments[-1]
    return value.upper()


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _cents_from_market_field(market: dict, stem: str) -> Decimal | None:
    dollars = _decimal(market.get(f"{stem}_dollars"))
    if dollars is not None:
        return dollars * 100
    return _decimal(market.get(stem))


def extract_bid_cents(market: dict, side: str = "yes") -> Decimal | None:
    return _cents_from_market_field(market, f"{side}_bid")


def extract_ask_cents(market: dict, side: str = "yes") -> Decimal | None:
    """Executable ask, using the opposite bid identity when needed."""
    direct = _cents_from_market_field(market, f"{side}_ask")
    if direct is not None:
        return direct
    opposite = "no" if side == "yes" else "yes"
    opposite_bid = extract_bid_cents(market, opposite)
    return Decimal(100) - opposite_bid if opposite_bid is not None else None


_NUMBER = re.compile(r"[-+]?(?:\d[\d,]*\.?\d*|\.\d+)")
_STRICT = re.compile(r"\b(?:above|over|greater\s+than|more\s+than)\b|>", re.I)
_INCLUSIVE = re.compile(r"\b(?:at\s+least|or\s+more|minimum)\b|≥|>=", re.I)
_BRACKET = re.compile(
    r"(?:between\s+)?\$?\s*([-+]?\d[\d,]*(?:\.\d+)?)\s*(?:-|–|—|to)\s*\$?\s*([-+]?\d[\d,]*(?:\.\d+)?)",
    re.I,
)


@dataclass(frozen=True)
class ThresholdInfo:
    threshold: Decimal
    operator: str
    source: str


def _strike_value(value: object) -> Decimal | None:
    if isinstance(value, dict):
        for key in ("value", "strike", "price", "threshold"):
            parsed = _decimal(value.get(key))
            if parsed is not None:
                return parsed
        return None
    return _decimal(value)


def extract_threshold(market: dict) -> ThresholdInfo:
    """Extract a cumulative rung boundary and explicitly identify its operator."""
    text = " ".join(
        str(market.get(key) or "")
        for key in ("yes_sub_title", "subtitle", "title", "rules_primary")
    ).strip()
    operator = ">=" if _INCLUSIVE.search(text) else ">" if _STRICT.search(text) else "unknown"

    for key in ("floor_strike", "custom_strike", "cap_strike"):
        threshold = _strike_value(market.get(key))
        if threshold is not None:
            # Kalshi floor strikes describe the lower boundary of an above rung.
            if operator == "unknown" and key == "floor_strike":
                operator = ">"
            return ThresholdInfo(threshold, operator, key)

    match = _STRICT.search(text) or _INCLUSIVE.search(text)
    if match:
        number = _NUMBER.search(text, match.end())
        if number:
            return ThresholdInfo(Decimal(number.group(0).replace(",", "")), operator, "market text")
    raise KalshiIngestError(f"Could not extract a scalar threshold from {market.get('ticker') or 'market'}")


def extract_bracket(market: dict) -> tuple[Decimal, Decimal] | None:
    text = " ".join(str(market.get(key) or "") for key in ("yes_sub_title", "subtitle", "title"))
    match = _BRACKET.search(text)
    if not match:
        return None
    return Decimal(match.group(1).replace(",", "")), Decimal(match.group(2).replace(",", ""))


def displayed_probability(market: dict) -> Decimal | None:
    """Return a displayed/last YES chance as a fraction, if exposed."""
    for stem in ("yes_probability", "yes_price", "last_price"):
        cents = _cents_from_market_field(market, stem)
        if cents is not None and Decimal(0) <= cents <= Decimal(100):
            return cents / 100
    return None


def market_probability(market: dict, source: str = "mid") -> tuple[Decimal | None, str]:
    if source == "displayed":
        shown = displayed_probability(market)
        return shown, "displayed"
    yes_bid = extract_bid_cents(market, "yes")
    yes_ask = extract_ask_cents(market, "yes")
    if yes_bid is not None and yes_ask is not None:
        return (yes_bid + yes_ask) / Decimal(200), "mid"
    shown = displayed_probability(market)
    return shown, "displayed fallback"


@dataclass(frozen=True)
class LadderRung:
    market_id: str
    threshold: Decimal
    operator: str
    yes_ask_cents: int | None
    no_ask_cents: int | None
    probability_above: Decimal | None
    probability_source: str
    status: str
    subtitle: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["threshold"] = float(self.threshold)
        payload["probability_above"] = (
            float(self.probability_above) if self.probability_above is not None else None
        )
        return payload


@dataclass(frozen=True)
class LadderSnapshot:
    event_ticker: str
    title: str
    rungs: tuple[LadderRung, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "event_ticker": self.event_ticker,
            "title": self.title,
            "rungs": [rung.to_dict() for rung in self.rungs],
            "warnings": list(self.warnings),
        }


class KalshiClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 15, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def _request(self, path: str, params: dict | None = None) -> dict:
        url = self.base_url + path
        if params:
            url += "?" + urlencode(params)
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
                with urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                if exc.code != 429 and not 500 <= exc.code < 600:
                    raise KalshiIngestError(f"Kalshi returned HTTP {exc.code}") from exc
                last_error = exc
            except (URLError, TimeoutError, socket.timeout, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < self.max_retries:
                time.sleep(min(4, 0.5 * (2**attempt)) + random.uniform(0, 0.15))
        raise KalshiIngestError(f"Kalshi request failed: {last_error}")

    def get_event_markets(self, event_ticker: str) -> list[dict]:
        markets: list[dict] = []
        cursor: str | None = None
        while True:
            params: dict[str, object] = {"event_ticker": event_ticker, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            response = self._request("/markets", params)
            markets.extend(response.get("markets") or [])
            cursor = response.get("cursor")
            if not cursor:
                return markets

    def get_orderbook_bid_levels(self, ticker: str, side: str = "yes") -> list[tuple[Decimal, Decimal]]:
        response = self._request(f"/markets/{quote(ticker, safe='')}/orderbook")
        book = response.get("orderbook_fp") or response.get("orderbook") or {}
        levels = book.get(f"{side}_dollars")
        if levels is not None:
            return [(Decimal(str(price)) * 100, Decimal(str(size))) for price, size in levels]
        return [(Decimal(str(price)), Decimal(str(size))) for price, size in (book.get(side) or [])]


def _int_cents(value: Decimal | None) -> int | None:
    return int(value) if value is not None else None


def ingest_markets(event_ticker: str, markets: Iterable[dict], probability_source: str = "mid") -> LadderSnapshot:
    rows = list(markets)
    if not rows:
        raise KalshiIngestError(f"No markets found for {event_ticker}")
    rungs: list[LadderRung] = []
    warnings: list[str] = []
    unsupported_brackets = 0
    for market in rows:
        try:
            info = extract_threshold(market)
        except KalshiIngestError:
            if extract_bracket(market):
                unsupported_brackets += 1
            continue
        if info.operator == ">=":
            warnings.append(f"{market.get('ticker')}: inclusive boundary (≥) confirmed; review exact-strike outcomes.")
        elif info.operator == "unknown":
            warnings.append(f"{market.get('ticker')}: boundary operator could not be confirmed.")
        probability, actual_source = market_probability(market, probability_source)
        rungs.append(
            LadderRung(
                market_id=str(market.get("ticker") or ""),
                threshold=info.threshold,
                operator=info.operator,
                yes_ask_cents=_int_cents(extract_ask_cents(market, "yes")),
                no_ask_cents=_int_cents(extract_ask_cents(market, "no")),
                probability_above=probability,
                probability_source=actual_source,
                status=str(market.get("status") or "unknown"),
                subtitle=str(market.get("yes_sub_title") or market.get("subtitle") or market.get("title") or ""),
            )
        )
    if unsupported_brackets and not rungs:
        raise KalshiIngestError(
            "This event exposes bracket contracts rather than cumulative Above contracts; direct bracket positions are not yet tradable in this build."
        )
    if not rungs:
        raise KalshiIngestError("Markets were found, but none formed a scalar Above ladder")
    rungs.sort(key=lambda rung: rung.threshold)
    title = str(rows[0].get("event_title") or rows[0].get("title") or event_ticker)
    return LadderSnapshot(event_ticker, title, tuple(rungs), tuple(dict.fromkeys(warnings)))


def fetch_ladder(event_link_or_ticker: str, probability_source: str = "mid", client: KalshiClient | None = None) -> LadderSnapshot:
    ticker = parse_event_ticker(event_link_or_ticker)
    if not ticker:
        raise KalshiIngestError("Paste a Kalshi event link or ticker")
    api = client or KalshiClient()
    return ingest_markets(ticker, api.get_event_markets(ticker), probability_source)
