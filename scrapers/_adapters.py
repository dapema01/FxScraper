"""Adapters that translate each scraper's raw row dicts into UnifiedRate
objects in the project's canonical shape.

Each per-bank CSV exposes a uniform set of columns:

    - pair / base_currency / quote_currency
    - quoted_per_units  (1 or 100; how many units of base_currency the
                         bank's raw rate refers to)
    - bid_per_unit / ask_per_unit  (normalized to quote_currency per 1 unit
                                    of base_currency)
    - bank-specific raw columns (kept for traceability)

So the adapters all do the same thing:
    bid           <- bid_per_unit
    ask           <- ask_per_unit
    raw_bid       <- the bank's raw bid number
    raw_ask       <- the bank's raw ask number
    raw_quoted_per_units <- the bank's `quoted_per_units` value

The only per-bank logic left is which column holds the "raw" rate (since
each bank names it differently) and how to normalize the bank's date.

Handelsbanken is the exception: its bid/ask are interbank quotes (not
comparable to retail), so we only expose `mid` in the unified output.
The raw bid/ask are still preserved in the per-bank CSV.
"""

from datetime import datetime
from typing import Iterable, List, Optional

from utils.unified import UnifiedRate, now_iso


def _to_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalize_dnb_date(raw: Optional[str]) -> Optional[str]:
    # DNB format: "26.05.2026 09:00"
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%d.%m.%Y %H:%M").date().isoformat()
    except ValueError:
        return raw


def _normalize_nordea_timestamp(raw: Optional[str]) -> Optional[str]:
    # Nordea format: "2026-05-27T07:11:22.966896Z" -> "2026-05-27"
    if not raw:
        return None
    return raw.split("T", 1)[0]


def _normalize_seb_date(raw: Optional[str], scraped_year: int) -> Optional[str]:
    # SEB format: "27/5" (day/month, no year). Assume current year from scrape.
    if not raw:
        return None
    try:
        day, month = raw.split("/", 1)
        return f"{scraped_year:04d}-{int(month):02d}-{int(day):02d}"
    except (ValueError, AttributeError):
        return raw


def from_dnb_rows(raw_rows: Iterable[dict], scraped_at: Optional[str] = None) -> List[UnifiedRate]:
    scraped_at = scraped_at or now_iso()
    out = []

    for row in raw_rows:
        base = row.get("base_currency") or row.get("currency")
        if not base:
            continue

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="DNB",
                base_currency=base,
                quote_currency=row.get("quote_currency") or "SEK",
                bid=_to_float(row.get("bid_per_unit")),
                ask=_to_float(row.get("ask_per_unit")),
                raw_bid=_to_float(row.get("buy_rate")),
                raw_ask=_to_float(row.get("sell_rate")),
                raw_quoted_per_units=_to_int(row.get("quoted_per_units")),
                rate_date=_normalize_dnb_date(row.get("updated")),
                source_bank_label=row.get("country"),
            )
        )

    return out


def from_nordea_rows(raw_rows: Iterable[dict], scraped_at: Optional[str] = None) -> List[UnifiedRate]:
    scraped_at = scraped_at or now_iso()
    out = []

    for row in raw_rows:
        base = row.get("base_currency")
        if not base:
            continue

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="Nordea",
                base_currency=base,
                quote_currency=row.get("quote_currency") or "SEK",
                bid=_to_float(row.get("bid_per_unit")),
                ask=_to_float(row.get("ask_per_unit")),
                raw_bid=_to_float(row.get("bid_sek_per_fx")),
                raw_ask=_to_float(row.get("ask_sek_per_fx")),
                raw_quoted_per_units=_to_int(row.get("quoted_per_units")),
                rate_date=_normalize_nordea_timestamp(row.get("ask_timestamp")),
            )
        )

    return out


def from_seb_rows(raw_rows: Iterable[dict], scraped_at: Optional[str] = None) -> List[UnifiedRate]:
    scraped_at = scraped_at or now_iso()
    # Use the scrape year to fill in SEB's missing year. Good enough as long
    # as we scrape on the same day the bank publishes (which we do daily).
    scrape_year = datetime.fromisoformat(scraped_at.replace("Z", "+00:00")).year
    out = []

    for row in raw_rows:
        base = row.get("base_currency") or row.get("currency")
        if not base:
            continue

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="SEB",
                base_currency=base,
                quote_currency=row.get("quote_currency") or "SEK",
                bid=_to_float(row.get("bid_per_unit")),
                ask=_to_float(row.get("ask_per_unit")),
                raw_bid=_to_float(row.get("buy_rate")),
                raw_ask=_to_float(row.get("sell_rate")),
                raw_quoted_per_units=_to_int(row.get("quoted_per_units")),
                rate_date=_normalize_seb_date(row.get("date"), scrape_year),
                source_bank_label=row.get("country"),
            )
        )

    return out


def from_danske_bank_rows(raw_rows: Iterable[dict], scraped_at: Optional[str] = None) -> List[UnifiedRate]:
    scraped_at = scraped_at or now_iso()
    out = []

    for row in raw_rows:
        base = row.get("base_currency") or row.get("currency")
        if not base:
            continue

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="Danske Bank",
                base_currency=base,
                quote_currency=row.get("quote_currency") or "SEK",
                bid=_to_float(row.get("bid_per_unit")),
                ask=_to_float(row.get("ask_per_unit")),
                mid=_to_float(row.get("mid_rate")),
                raw_bid=_to_float(row.get("buy_from_abroad")),
                raw_ask=_to_float(row.get("sell_to_abroad")),
                raw_quoted_per_units=_to_int(row.get("quoted_per_units")),
                rate_date=row.get("date"),
                source_bank_label=row.get("name"),
            )
        )

    return out


def from_handelsbanken_rows(raw_rows: Iterable[dict], scraped_at: Optional[str] = None) -> List[UnifiedRate]:
    """Adapt Handelsbanken's raw rows to the unified schema.

    Handelsbanken's Millistream feed is an interbank/wholesale quote
    (~0.02% spread), not a retail customer rate like the other banks.
    Putting its bid/ask alongside DNB's, Nordea's, etc. in the unified
    file would be misleading — the numbers aren't comparable.

    Instead, we expose only the midpoint here (using `last_per_unit`, which
    is Handelsbanken's official mid normalized to per-1-unit; falling back
    to (bid_per_unit + ask_per_unit) / 2 if last is missing). The raw
    bid/ask are still preserved in the per-bank CSV.
    """
    scraped_at = scraped_at or now_iso()
    out = []

    for row in raw_rows:
        base = row.get("base_currency")
        quote = row.get("quote_currency")
        # Skip non-SEK-quoted pairs if any sneak in.
        if not base or quote != "SEK":
            continue

        last_per_unit = _to_float(row.get("last_per_unit"))
        bid_per_unit = _to_float(row.get("bid_per_unit"))
        ask_per_unit = _to_float(row.get("ask_per_unit"))

        if last_per_unit is not None:
            mid = last_per_unit
        elif bid_per_unit is not None and ask_per_unit is not None:
            mid = round((bid_per_unit + ask_per_unit) / 2, 6)
        else:
            mid = None

        if mid is None:
            continue

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="Handelsbanken",
                base_currency=base,
                quote_currency=quote,
                bid=None,
                ask=None,
                mid=mid,
                spread=None,
                raw_bid=_to_float(row.get("bid_rate")),
                raw_ask=_to_float(row.get("ask_rate")),
                raw_quoted_per_units=_to_int(row.get("quoted_per_units")),
                source_bank_label=row.get("instrument_name"),
            )
        )

    return out