from csv import DictWriter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Tidy/long format: one row per (bank, currency_pair, scrape).
# This shape is what plotting and time-series tools (pandas, seaborn,
# plotly) expect. Adding a new bank means more rows, not more columns.
#
# Rate column conventions:
#   bid/ask/mid/spread   -> normalized to quote_currency per 1 unit of base_currency
#                           (comparable across banks).
#   raw_bid/raw_ask      -> per `raw_quoted_per_units` units of base_currency,
#                           exactly as the bank publishes. Useful for traceability
#                           and reproducing the bank's own number.
#   raw_quoted_per_units -> the multiplier the bank quotes in (1 or 100).
UNIFIED_FIELDNAMES = [
    "scraped_at",
    "bank",
    "pair",
    "base_currency",
    "quote_currency",
    "bid",
    "ask",
    "mid",
    "spread",
    "raw_bid",
    "raw_ask",
    "raw_quoted_per_units",
    "rate_date",
    "source_bank_label",
]


@dataclass
class UnifiedRate:
    """A single FX observation in the project's canonical shape.

    Conventions:
        - bid: the rate at which the bank BUYS base_currency from a customer
               (lower number).
        - ask: the rate at which the bank SELLS base_currency to a customer
               (higher number).
        - bid/ask/mid/spread are quoted as quote_currency per 1 unit of
          base_currency, always. If the bank published "5.82 SEK per 100 JPY",
          we store bid/ask as 0.0582 and put 5.82 in raw_bid/raw_ask with
          raw_quoted_per_units=100.
        - pair is auto-built as f"{base_currency}/{quote_currency}" if not set
          explicitly. All five banks currently produce <FX>/SEK pairs.
        - scraped_at is the canonical time axis for plotting (always set,
          always comparable across banks). rate_date is best-effort from
          the bank itself and may be missing or in an odd format.
    """

    scraped_at: str
    bank: str
    base_currency: str
    quote_currency: str = "SEK"
    pair: Optional[str] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    spread: Optional[float] = None
    raw_bid: Optional[float] = None
    raw_ask: Optional[float] = None
    raw_quoted_per_units: Optional[int] = None
    rate_date: Optional[str] = None
    source_bank_label: Optional[str] = None

    def __post_init__(self):
        # Auto-build the pair label so adapters never need to spell it out.
        if self.pair is None and self.base_currency and self.quote_currency:
            self.pair = f"{self.base_currency}/{self.quote_currency}"

        # Auto-fill mid and spread from bid/ask when not provided.
        # Round to 6 decimals — enough precision for FX rates, kills float noise.
        if self.bid is not None and self.ask is not None:
            if self.mid is None:
                self.mid = round((self.bid + self.ask) / 2, 6)
            if self.spread is None:
                self.spread = round(self.ask - self.bid, 6)


def now_iso():
    """UTC timestamp in ISO 8601 with seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_unified_csv(rates, output_file: Path, append: bool = False):
    """Write a list of UnifiedRate objects to a CSV file.

    Args:
        rates: iterable of UnifiedRate instances.
        output_file: destination path. Parent dir must exist.
        append: if True, append and skip the header when the file already has
                content. If False (default), overwrite.
    """
    output_file = Path(output_file)
    mode = "a" if append else "w"

    write_header = True
    if append and output_file.exists() and output_file.stat().st_size > 0:
        write_header = False

    with output_file.open(mode, newline="", encoding="utf-8") as f:
        writer = DictWriter(f, fieldnames=UNIFIED_FIELDNAMES)

        if write_header:
            writer.writeheader()

        for rate in rates:
            row = asdict(rate)
            # Guard against extra fields if the dataclass ever grows.
            writer.writerow({k: row.get(k) for k in UNIFIED_FIELDNAMES})

    return output_file