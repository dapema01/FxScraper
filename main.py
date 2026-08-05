import csv
import sys
from dataclasses import asdict
from pathlib import Path

from scrapers import (
    dnb_scraper,
    nordea_scraper,
    danske_bank_scraper,
    seb_scraper,
    handelsbanken_scraper,
    swedbank_scraper,
    swedbank_private_scraper
)
from scrapers._adapters import (
    from_dnb_rows,
    from_nordea_rows,
    from_seb_rows,
    from_danske_bank_rows,
    from_handelsbanken_rows,
    from_swedbank_rows,
    from_swedbank_private_rows
)
from utils import (
    UNIFIED_FIELDNAMES,
    get_output_dir,
    now_iso,
)


# Varje post: (scraper_callable, adapter_callable, per_bank_prefix).
# - Scrapern skriver sin daterade per-bank-CSV via get_dated_output_file().
# - Adaptern översätter råa rad-dictar till UnifiedRate-objekt.
# - per_bank_prefix låter orkestratorn sopa bort äldre daterade filer för
#   den banken så att vi bara behåller den senaste dagen på disk (speglar
#   det rullande all_banks-beteendet).
SCRAPER_PIPELINE = [
    (dnb_scraper,               from_dnb_rows),
    (nordea_scraper,            from_nordea_rows),
    (seb_scraper,               from_seb_rows),
    (danske_bank_scraper,       from_danske_bank_rows),
    (handelsbanken_scraper,     from_handelsbanken_rows),
    (swedbank_scraper,          from_swedbank_rows),
    (swedbank_private_scraper,  from_swedbank_private_rows)
]


ALL_BANKS_PREFIX = "all_banks_"


def _read_csv_rows(path: Path):
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _find_existing_rolling_file(output_dir: Path, prefix: str) -> Path | None:
    """Returnera den senaste befintliga <prefix>*.csv, eller None.

    ISO-datum i filnamn sorteras lexikalt i datumordning, så en enkel
    sortering ger oss den senaste.
    """
    candidates = sorted(output_dir.glob(f"{prefix}*.csv"))
    return candidates[-1] if candidates else None


def _load_existing_rows(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    return _read_csv_rows(path)


def _drop_today_rows(rows: list[dict], today: str) -> list[dict]:
    """Ta bort rader som scrapats tidigare idag (så att omkörningar ersätter, inte duplicerar)."""
    return [r for r in rows if not (r.get("scraped_at") or "").startswith(today)]


def _write_unified_rolling(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=UNIFIED_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in UNIFIED_FIELDNAMES})


def main():
    scraped_at = now_iso()
    today = scraped_at[:10]

    new_rows: list[dict] = []
    failures: list[str] = []
    output_dir = get_output_dir()

    for scraper, adapter in SCRAPER_PIPELINE:
        try:

            raw_rows = scraper()
            unified = adapter(raw_rows, scraped_at=scraped_at)
            new_rows.extend(asdict(u) for u in unified)
            print(f"  Normalized: {len(unified)} rows from {scraper.__name__}")

        except Exception as e:
            print(f"Failed: {scraper.__name__} -> {e}")
            failures.append(scraper.__name__)

    if not new_rows:
        print("\nNo new rows to write to the unified file.")
    else:
        # all_banks-rullning: ladda historiken, släng dagens tidigare rader
        # (så att omkörningar ersätter rent), lägg till dagens, skriv till
        # dagens filnamn, radera den gamla rullande filen om den hade ett
        # annat datum.
        existing_path = _find_existing_rolling_file(output_dir, ALL_BANKS_PREFIX)
        existing_rows = _load_existing_rows(existing_path)
        existing_rows = _drop_today_rows(existing_rows, today)

        combined = existing_rows + new_rows
        target_path = output_dir / f"{ALL_BANKS_PREFIX}{today}.csv"
        _write_unified_rolling(combined, target_path)

        if existing_path is not None and existing_path != target_path:
            existing_path.unlink()
            print(f"Renamed rolling file: {existing_path.name} -> {target_path.name}")

        print(
            f"\nRolling file: {target_path} "
            f"({len(combined)} total rows, {len(new_rows)} added today)"
        )

    # Avsluta med nollskild kod om någon scraper misslyckades, så att CI
    # (och retry-jobbet) ser det.
    if failures:
        print(f"\n{len(failures)} scraper(s) failed: {', '.join(failures)}")
        sys.exit(1)


if __name__ == "__main__":
    main()