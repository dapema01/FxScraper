from csv import DictWriter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Tidy/long format: one row per (bank, currency, scrape).
# This shape is what plotting and time-series tools (pandas, seaborn,
# plotly) expect. Adding a new bank means more rows, not more columns.
UNIFIED_FIELDNAMES = [
    "scraped_at",
    "bank",
    "currency",
    "bid",
    "ask",
    "mid",
    "spread",
    "rate_date",
    "source_bank_label",
]


@dataclass
class UnifiedRate:
    """A single FX observation in the project's canonical shape.

    Conventions:
        - bid: the rate at which the bank BUYS foreign currency from a customer
               (lower number).
        - ask: the rate at which the bank SELLS foreign currency to a customer
               (higher number).
        - All rates are per 1 unit of the foreign currency, quoted in SEK.
        - scraped_at is the canonical time axis for plotting (always set,
          always comparable across banks). rate_date is best-effort from
          the bank itself and may be missing or in an odd format.
    """

    scraped_at: str
    bank: str
    currency: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    spread: Optional[float] = None
    rate_date: Optional[str] = None
    source_bank_label: Optional[str] = None

    def __post_init__(self):
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