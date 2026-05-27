import csv
from datetime import date
from pathlib import Path
from dataclasses import asdict

from scrapers import (
    dnb_scraper,
    nordea_scraper,
    danske_bank_scraper,
    seb_scraper,
    handelsbanken_scraper,
)
from scrapers._adapters import (
    from_dnb_rows,
    from_nordea_rows,
    from_seb_rows,
    from_danske_bank_rows,
    from_handelsbanken_rows,
)
from utils import (
    UNIFIED_FIELDNAMES,
    get_output_dir,
    now_iso,
)


# Each entry: (scraper_callable, adapter_callable).
# The adapter translates that scraper's raw row dicts into UnifiedRate objects.
SCRAPER_PIPELINE = [
    (dnb_scraper, from_dnb_rows),
    (nordea_scraper, from_nordea_rows),
    (seb_scraper, from_seb_rows),
    (danske_bank_scraper, from_danske_bank_rows),
    (handelsbanken_scraper, from_handelsbanken_rows),
]


ALL_BANKS_PREFIX = "all_banks_"


def _read_csv_rows(path: Path):
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _find_existing_rolling_file(output_dir: Path) -> Path | None:
    """Return the most recent existing all_banks_*.csv, or None if none exist.

    Sort by filename (ISO dates sort lexically, so this is also date order).
    """
    candidates = sorted(output_dir.glob(f"{ALL_BANKS_PREFIX}*.csv"))
    return candidates[-1] if candidates else None


def _load_existing_rows(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    return _read_csv_rows(path)


def _drop_today_rows(rows: list[dict], today: str) -> list[dict]:
    """Remove rows scraped earlier today (so re-runs replace, not duplicate).

    scraped_at is ISO 8601 like '2026-05-27T08:30:00+00:00'; we compare on the
    date prefix.
    """
    return [r for r in rows if not (r.get("scraped_at") or "").startswith(today)]


def _write_rolling_file(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=UNIFIED_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in UNIFIED_FIELDNAMES})


def main():
    scraped_at = now_iso()
    today = scraped_at[:10]  # 'YYYY-MM-DD'

    new_rows: list[dict] = []

    for scraper, adapter in SCRAPER_PIPELINE:
        try:
            output_file = scraper()
            print(f"Finished: {scraper.__name__} -> {output_file}")

            raw_rows = _read_csv_rows(output_file)
            unified = adapter(raw_rows, scraped_at=scraped_at)
            # Convert dataclass instances to plain dicts for the rolling file.
            new_rows.extend(asdict(u) for u in unified)
            print(f"  Normalized: {len(unified)} rows from {scraper.__name__}")

        except Exception as e:
            print(f"Failed: {scraper.__name__} -> {e}")

    if not new_rows:
        print("\nNo new rows to write.")
        return

    output_dir = get_output_dir()

    # Find the previous rolling file (if any), load its history, drop any
    # rows from today (so re-runs replace cleanly), then append today's rows.
    existing_path = _find_existing_rolling_file(output_dir)
    existing_rows = _load_existing_rows(existing_path)
    existing_rows = _drop_today_rows(existing_rows, today)

    combined = existing_rows + new_rows

    # Write to today's filename. If the previous file had a different date,
    # remove it so we don't end up with two rolling files.
    target_path = output_dir / f"{ALL_BANKS_PREFIX}{today}.csv"
    _write_rolling_file(combined, target_path)

    if existing_path is not None and existing_path != target_path:
        existing_path.unlink()
        print(f"Renamed rolling file: {existing_path.name} -> {target_path.name}")

    print(
        f"\nRolling file: {target_path} "
        f"({len(combined)} total rows, {len(new_rows)} added today)"
    )


if __name__ == "__main__":
    main()