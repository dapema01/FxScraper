import csv
import json
from pathlib import Path
import sys

from botasaurus.browser import browser, Driver

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import get_dated_output_file


SEB_URL = "https://seb.se/ssc/trading/fx-rates-bff/api/rates/avista"


FIELDNAMES = [
    "country",
    "currency",
    "buy_rate",
    "sell_rate",
    "date",
]


# SEB returnerar en tabell med headers + rows. Varje row har en "data"-lista
# i samma ordning som headers: [Land, Valuta, Köpkurs, Säljkurs, Datum].
COLUMN_INDEX = {
    "country": 0,
    "currency": 1,
    "buy_rate": 2,
    "sell_rate": 3,
    "date": 4,
}


def _cell_value(row_data, index):
    if index >= len(row_data):
        return None

    cell = row_data[index]
    if not isinstance(cell, dict):
        return None

    return cell.get("value")


def parse_seb_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected SEB payload to be a dict with headers/rows")

    raw_rows = payload.get("rows", [])
    if not raw_rows:
        raise ValueError("SEB payload contained no rows")

    parsed_rows = []

    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            continue

        data = raw_row.get("data")
        if not isinstance(data, list):
            continue

        currency = _cell_value(data, COLUMN_INDEX["currency"])
        if not currency:
            continue

        parsed_rows.append(
            {
                "country": _cell_value(data, COLUMN_INDEX["country"]),
                "currency": currency,
                "buy_rate": _cell_value(data, COLUMN_INDEX["buy_rate"]),
                "sell_rate": _cell_value(data, COLUMN_INDEX["sell_rate"]),
                "date": _cell_value(data, COLUMN_INDEX["date"]),
            }
        )

    if not parsed_rows:
        raise ValueError("No usable SEB rows found")

    return parsed_rows


def write_rows_to_csv(rows, output_file):
    with output_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


@browser(
    headless=True,
    reuse_driver=False,
    close_on_crash=True,
)
def _scrape_seb(driver: Driver, data):
    driver.get(SEB_URL)

    # Webbläsaren renderar JSON som text i body. Plocka ut råtexten.
    text = driver.run_js("return document.body ? document.body.innerText : '';")

    if not text:
        raise RuntimeError("SEB endpoint returned empty body")

    return text


def seb_scraper():
    output_file = get_dated_output_file("seb_rates")

    text = _scrape_seb()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse SEB response as JSON: {e}") from e

    rows = parse_seb_payload(payload)

    write_rows_to_csv(rows, output_file)

    print(f"SEB rates saved to {output_file}")

    return output_file


if __name__ == "__main__":
    seb_scraper()