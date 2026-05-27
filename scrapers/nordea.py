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


# All rates are quoted as SEK per 1 unit of foreign currency (SEK = quote).
#
#   base_currency  : the foreign currency (USD, EUR, ...)
#   quote_currency : always SEK in this scraper
#   ask_sek_per_fx : SEK Nordea charges to SELL 1 unit of base_currency to you
#                    (= what the bank sells FX for, = customer's buy price)
#   bid_sek_per_fx : SEK Nordea pays to BUY 1 unit of base_currency from you
#                    (= what the bank buys FX for, = customer's sell price)
#
# By construction ask >= bid; the difference is Nordea's spread.
FIELDNAMES = [
    "pair",
    "base_currency",
    "quote_currency",
    "quoted_per_units",
    "bid_per_unit",
    "ask_per_unit",
    "ask_sek_per_fx",
    "bid_sek_per_fx",
    "ask_timestamp",
    "bid_timestamp",
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


def fetch_bid_rate(session, currency):
    """Hämta Nordeas köpkurs för `currency` uttryckt i SEK per 1 enhet.

    Endpoint: basecurrency=<FX>, exchangecurrency=SEK
        -> hur många SEK Nordea ger dig för 1 enhet av valutan.
    """
    bid_url = (
        "https://www.nordea.se/nd/api/dbf/ca/currencies-v2/open/exchange/"
        f"basecurrency/{currency}/exchangecurrency/SEK"
        "?country_code=se&markup=true"
    )

    try:
        bid_payload = fetch_json(session, bid_url, f"bid rate for {currency}")
        return {
            "bid_sek_per_fx": bid_payload.get("show_rate"),
            "bid_timestamp": bid_payload.get("timestamp"),
        }

    except RuntimeError as e:
        print(f"Warning: {e}")
        return {
            "bid_sek_per_fx": None,
            "bid_timestamp": None,
        }


def write_rows_to_csv(rows, output_file):
    # Overwrite each run: the per-bank CSV is "today's snapshot".
    # Intraday/historical accumulation lives in the rolling all_banks file.
    with output_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def nordea_scraper():
    output_file = get_dated_output_file("nordea_rates")

    with requests.Session() as session:
        # Sälj-endpointen (basecurrency=SEK) ger alla säljkurser i ett svep:
        # hur många SEK kunden betalar för 1 enhet av respektive valuta.
        ask_payload = fetch_json(session, NORDEA_SELL_URL, "Nordea ask rates")

        ask_rows = ask_payload.get("result", [])
        if not ask_rows:
            raise ValueError("Nordea ask endpoint returned no rows")

        combined_rows = []

        for ask_item in ask_rows:
            currency = ask_item.get("exchange_currency_code")
            if not currency:
                continue

            bid_data = fetch_bid_rate(session, currency)

            ask_rate = ask_item.get("show_rate")
            bid_rate = bid_data["bid_sek_per_fx"]

            combined_rows.append(
                {
                    "pair": f"{currency}/SEK",
                    "base_currency": currency,
                    "quote_currency": "SEK",
                    "quoted_per_units": 1,
                    "bid_per_unit": bid_rate,
                    "ask_per_unit": ask_rate,
                    "ask_sek_per_fx": ask_rate,
                    "bid_sek_per_fx": bid_rate,
                    "ask_timestamp": ask_item.get("timestamp"),
                    "bid_timestamp": bid_data["bid_timestamp"],
                }
            )

    if not combined_rows:
        raise ValueError("No usable Nordea rows found")

    write_rows_to_csv(combined_rows, output_file)

    print(f"Nordea rates saved to {output_file}")

    return output_file


if __name__ == "__main__":
    nordea_scraper()