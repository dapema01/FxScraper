"""Adapters that translate each scraper's raw row dicts into UnifiedRate
objects in the project's canonical shape.

Buy/sell semantics differ between banks (see project notes), so per-bank
translation matters:

    - DNB:           buy_rate_per_unit -> bid, sell_rate_per_unit -> ask
    - SEB:           buy_rate          -> bid, sell_rate          -> ask
    - Danske Bank:   buy_from_abroad   -> bid, sell_to_abroad     -> ask
    - Nordea:        buy_rate          -> bid, sell_rate          -> ask
                     (Nordea labels the bank-side rate, which happens
                      to coincide with the bid/ask convention.)
    - Handelsbanken: last_rate         -> mid; bid/ask intentionally
                     dropped from unified output because the Millistream
                     feed is an interbank quote (~0.02% spread), not
                     comparable to the other banks' retail bid/ask. The
                     raw bid/ask is still preserved in the per-bank CSV.
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
        currency = row.get("currency")
        if not currency:
            continue

        rate_date = _normalize_dnb_date(row.get("updated"))

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="DNB",
                currency=currency,
                bid=_to_float(row.get("buy_rate_per_unit")),
                ask=_to_float(row.get("sell_rate_per_unit")),
                rate_date=rate_date,
                source_bank_label=row.get("country"),
            )
        )

    return out


def from_nordea_rows(raw_rows: Iterable[dict], scraped_at: Optional[str] = None) -> List[UnifiedRate]:
    scraped_at = scraped_at or now_iso()
    out = []

    for row in raw_rows:
        currency = row.get("currency")
        if not currency:
            continue

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="Nordea",
                currency=currency,
                bid=_to_float(row.get("buy_rate")),
                ask=_to_float(row.get("sell_rate")),
                rate_date=_normalize_nordea_timestamp(row.get("sell_timestamp")),
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
        currency = row.get("currency")
        if not currency:
            continue

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="SEB",
                currency=currency,
                bid=_to_float(row.get("buy_rate")),
                ask=_to_float(row.get("sell_rate")),
                rate_date=_normalize_seb_date(row.get("date"), scrape_year),
                source_bank_label=row.get("country"),
            )
        )

    return out


def from_danske_bank_rows(raw_rows: Iterable[dict], scraped_at: Optional[str] = None) -> List[UnifiedRate]:
    scraped_at = scraped_at or now_iso()
    out = []

    for row in raw_rows:
        currency = row.get("currency")
        if not currency:
            continue

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="Danske Bank",
                currency=currency,
                bid=_to_float(row.get("buy_from_abroad")),
                ask=_to_float(row.get("sell_to_abroad")),
                mid=_to_float(row.get("mid_rate")),
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

    Instead, we expose only the midpoint here (using `last_rate`, which
    Handelsbanken publishes as the official mid; falling back to
    (bid+ask)/2 if last is missing). The raw bid/ask are still preserved
    in handelsbanken_rates_*.csv for traceability.
    """
    scraped_at = scraped_at or now_iso()
    out = []

    for row in raw_rows:
        currency = row.get("base_currency")
        # Skip non-SEK-quoted pairs if any sneak in.
        if not currency or row.get("quote_currency") != "SEK":
            continue

        last = _to_float(row.get("last_rate"))
        bid = _to_float(row.get("bid_rate"))
        ask = _to_float(row.get("ask_rate"))

        # Prefer Handelsbanken's published last (= official mid).
        # Fall back to (bid+ask)/2 only if last is missing.
        if last is not None:
            mid = last
        elif bid is not None and ask is not None:
            mid = round((bid + ask) / 2, 6)
        else:
            mid = None

        if mid is None:
            continue

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="Handelsbanken",
                currency=currency,
                bid=None,
                ask=None,
                mid=mid,
                spread=None,
                source_bank_label=row.get("instrument_name"),
            )
        )

    return out