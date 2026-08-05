"""Intraday-orkestrator.

Samma pipeline som main.py (scrapa alla banker, normalisera till det
enhetliga schemat), men avsedd att köras flera gånger per dag och skriva
till en egen, månadsroterande fil.

Skillnader mot main.py:
    - Utdata går till intraday_ÅÅÅÅ-MM.csv (en fil per månad). Vid
      månadsskifte börjar en ny fil automatiskt; ingen manuell rotation
      behövs och ingen enskild fil växer sig ohanterlig (~426 rader/dag
      vid 3 körningar -> ~13 000 rader/månad).
    - Dedup sker på exakt scraped_at (sekundprecision), inte på hela dagen.
      De tre dagliga körningarna har olika tidsstämplar och behålls därför
      alla. En oavsiktlig omkörning i exakt samma sekund ersätter i stället
      för att duplicera.

Schemaläggning (exempel, cron, 08/13/18 på vardagar och helger):
    0 8,13,18 * * * cd /sökväg/till/projekt && /sökväg/till/venv/bin/python intraday.py >> logs/intraday.log 2>&1
"""

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


# Varje post: (scraper_callable, adapter_callable).
# - Scrapern returnerar råa rad-dictar direkt (ingen per-bank-CSV skrivs).
# - Adaptern översätter dem till UnifiedRate-objekt.
SCRAPER_PIPELINE = [
    (dnb_scraper,               from_dnb_rows),
    (nordea_scraper,            from_nordea_rows),
    (seb_scraper,               from_seb_rows),
    (danske_bank_scraper,       from_danske_bank_rows),
    (handelsbanken_scraper,     from_handelsbanken_rows),
    (swedbank_scraper,          from_swedbank_rows),
    (swedbank_private_scraper,  from_swedbank_private_rows)
]


# Egen namnrymd så att intraday-filerna inte fångas av main.py:s
# all_banks_*.csv-glob (och vice versa).
INTRADAY_PREFIX = "intraday_"


def _read_csv_rows(path: Path):
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _month_file(output_dir: Path, month: str) -> Path:
    """Sökväg till den månadens intraday-fil, t.ex. intraday_2026-08.csv."""
    return output_dir / f"{INTRADAY_PREFIX}{month}.csv"


def _load_existing_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return _read_csv_rows(path)


def _drop_scrape(rows: list[dict], scraped_at: str) -> list[dict]:
    """Ta bort rader med exakt denna scraped_at (gör en omkörning idempotent
    utan att röra andra snapshots samma dag)."""
    return [r for r in rows if (r.get("scraped_at") or "") != scraped_at]


def _write_unified(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=UNIFIED_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in UNIFIED_FIELDNAMES})


def main():
    scraped_at = now_iso()
    month = scraped_at[:7]  # "ÅÅÅÅ-MM" -> månadsbucket

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
        print("\nNo new rows to write to the intraday file.")
    else:
        # Månadsrotation: ladda denna månads fil (om den finns), släng ev.
        # rader med exakt samma scraped_at (idempotent omkörning), lägg till
        # dagens snapshot och skriv tillbaka.
        target_path = _month_file(output_dir, month)
        existing_rows = _load_existing_rows(target_path)
        existing_rows = _drop_scrape(existing_rows, scraped_at)

        combined = existing_rows + new_rows
        _write_unified(combined, target_path)

        print(
            f"\nIntraday file: {target_path} "
            f"({len(combined)} total rows, {len(new_rows)} added this run)"
        )

    if failures:
        print(f"\n{len(failures)} scraper(s) failed: {', '.join(failures)}")
        sys.exit(1)


if __name__ == "__main__":
    main()