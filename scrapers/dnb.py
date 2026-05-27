import csv
from pathlib import Path
import sys

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import get_dated_output_file


DNB_URL = (
    "https://www.dnb.se/portalfront/datafiles/miscellaneous/csv/"
    "kursliste_over_SEK.csv?_=1774742606760"
)

FIELDNAMES = [
    "updated",
    "country",
    "pair",
    "base_currency",
    "quote_currency",
    "quoted_per_units",
    "bid_per_unit",
    "ask_per_unit",
    "buy_rate",
    "sell_rate",
    "change",
    "settlement_price",
    "unit",
    "buy_rate_per_unit",
    "sell_rate_per_unit",
]


def fetch_csv_text(url):
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.text

    except requests.exceptions.Timeout as e:
        raise RuntimeError("Timeout while fetching DNB rates") from e

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else "unknown"
        raise RuntimeError(
            f"HTTP error while fetching DNB rates: {status_code}"
        ) from e

    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Request failed while fetching DNB rates: {e}") from e


def parse_float(value, default=None):
    value = value.strip()

    if not value:
        return default

    return float(value)


def parse_dnb_csv(text):
    rows = []
    reader = csv.reader(text.splitlines())

    updated = None

    for i, row in enumerate(reader):
        if not row:
            continue

        # First row looks like:
        # 27.03.2026 09:00,,,,,,,,,
        if i == 0:
            updated = row[0].strip()
            continue

        # Expected format:
        # country, unit, code, empty, buy, sell, change, settlementprice
        if len(row) < 8:
            continue

        country = row[0].strip()
        unit = row[1].strip()
        currency = row[2].strip()
        buy_rate = row[4].strip()
        sell_rate = row[5].strip()
        change = row[6].strip()
        settlement_price = row[7].strip()

        if not currency:
            continue

        unit_value = parse_float(unit, default=1.0)
        buy_value = parse_float(buy_rate)
        sell_value = parse_float(sell_rate)
        change_value = parse_float(change)
        settlement_price_value = parse_float(settlement_price)

        rows.append(
            {
                "updated": updated,
                "country": country,
                "pair": f"{currency}/SEK",
                "base_currency": currency,
                "quote_currency": "SEK",
                "quoted_per_units": int(unit_value) if unit_value == int(unit_value) else unit_value,
                "bid_per_unit": (
                    buy_value / unit_value if buy_value is not None else None
                ),
                "ask_per_unit": (
                    sell_value / unit_value if sell_value is not None else None
                ),
                "buy_rate": buy_value,
                "sell_rate": sell_value,
                "change": change_value,
                "settlement_price": settlement_price_value,
                "unit": unit_value,
                "buy_rate_per_unit": (
                    buy_value / unit_value if buy_value is not None else None
                ),
                "sell_rate_per_unit": (
                    sell_value / unit_value if sell_value is not None else None
                ),
            }
        )

    if not rows:
        raise ValueError("No usable DNB rows found")

    return rows


def write_rows_to_csv(rows, output_file):
    with output_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def dnb_scraper():
    output_file = get_dated_output_file("dnb_rates")

    text = fetch_csv_text(DNB_URL)
    rows = parse_dnb_csv(text)

    write_rows_to_csv(rows, output_file)

    print(f"DNB rates saved to {output_file}")

    return output_file


if __name__ == "__main__":
    dnb_scraper()