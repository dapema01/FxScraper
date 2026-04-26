import csv
from pathlib import Path
import sys

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import get_dated_output_file


NORDEA_SELL_URL = (
    "https://www.nordea.se/nd/api/dbf/ca/currencies-v2/open/exchange/"
    "basecurrency/SEK?country_code=se&markup=true"
)


HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nordea.se/",
}


FIELDNAMES = [
    "currency",
    "sell_rate",
    "buy_rate",
    "sell_timestamp",
    "buy_timestamp",
]



def fetch_json(session, url, description):
    try:
        response = session.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        return response.json()

    except requests.exceptions.Timeout as e:
        raise RuntimeError(f"Timeout while fetching {description}") from e

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else "unknown"
        raise RuntimeError(
            f"HTTP error while fetching {description}: {status_code}"
        ) from e

    except requests.exceptions.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON while fetching {description}") from e

    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Request failed while fetching {description}: {e}") from e


def fetch_buy_rate(session, currency):
    buy_url = (
        "https://www.nordea.se/nd/api/dbf/ca/currencies-v2/open/exchange/"
        f"basecurrency/{currency}/exchangecurrency/SEK"
        "?country_code=se&markup=true"
    )

    try:
        buy_payload = fetch_json(session, buy_url, f"buy rate for {currency}")
        return {
            "buy_rate": buy_payload.get("show_rate"),
            "buy_timestamp": buy_payload.get("timestamp"),
        }

    except RuntimeError as e:
        print(f"Warning: {e}")
        return {
            "buy_rate": None,
            "buy_timestamp": None,
        }


def write_rows_to_csv(rows, output_file):
    file_exists = output_file.exists()
    file_is_empty = not file_exists or output_file.stat().st_size == 0

    with output_file.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)

        if file_is_empty:
            writer.writeheader()

        writer.writerows(rows)


def nordea_scraper():
    output_file = get_dated_output_file("nordea_rates")

    with requests.Session() as session:
        sell_payload = fetch_json(session, NORDEA_SELL_URL, "Nordea sell rates")

        sell_rows = sell_payload.get("result", [])
        if not sell_rows:
            raise ValueError("Nordea sell endpoint returned no rows")

        combined_rows = []

        for sell_item in sell_rows:
            currency = sell_item.get("exchange_currency_code")
            if not currency:
                continue

            buy_data = fetch_buy_rate(session, currency)

            combined_rows.append(
                {
                    "currency": currency,
                    "sell_rate": sell_item.get("show_rate"),
                    "buy_rate": buy_data["buy_rate"],
                    "sell_timestamp": sell_item.get("timestamp"),
                    "buy_timestamp": buy_data["buy_timestamp"],
                }
            )

    if not combined_rows:
        raise ValueError("No usable Nordea rows found")

    write_rows_to_csv(combined_rows, output_file)

    print(f"Nordea rates saved to {output_file}")

    return output_file


if __name__ == "__main__":
    nordea_scraper()