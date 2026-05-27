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
    "currency",
    "name",
    "unit",
    "buy_rate",
    "sell_rate",
    "mid_rate",
    "timestamp",
]


def parse_seb_payload(payload):
    # SEB:s svar kommer som en lista, eller som ett objekt med t.ex. "rates".
    if isinstance(payload, dict):
        items = (
            payload.get("rates")
            or payload.get("result")
            or payload.get("data")
            or []
        )
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    rows = []

    for item in items:
        if not isinstance(item, dict):
            continue

        currency = (
            item.get("currency")
            or item.get("currencyCode")
            or item.get("code")
        )
        if not currency:
            continue

        rows.append(
            {
                "currency": currency,
                "name": item.get("name") or item.get("description"),
                "unit": item.get("unit") or item.get("quantity") or 1,
                "buy_rate": item.get("buyRate") or item.get("buy") or item.get("bid"),
                "sell_rate": item.get("sellRate") or item.get("sell") or item.get("ask"),
                "mid_rate": item.get("midRate") or item.get("mid"),
                "timestamp": (
                    item.get("timestamp")
                    or item.get("updated")
                    or item.get("rateDate")
                ),
            }
        )

    if not rows:
        raise ValueError("No usable SEB rows found")

    return rows


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

    # Webbläsaren renderar JSON som en <pre>-tagg eller i body. Plocka ut råtexten.
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
