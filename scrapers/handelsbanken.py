import csv
import json
from pathlib import Path
import sys

from botasaurus.browser import browser, Driver

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import get_dated_output_file


HANDELSBANKEN_URL = (
    "https://mws-2.millistream.com/mws.fcgi"
    "?cmd=quote"
    "&fields=name,insref,lastprice,diff1dprc,dayhighprice,daylowprice,"
    "diff1mprc,diffytdprc,diff1yprc,lastprice,bidprice,askprice,"
    "lowpriceytd,highpriceytd,factor"
    "&insref=926,150,72825,117083,72829,72828,72826,51623,117438,117521"
    "&filetype=json&lang=en"
    "&token=98d5ae3f-db2c-47ff-af03-b8cd1155d503"
)


FIELDNAMES = [
    "pair",
    "base_currency",
    "quote_currency",
    "quoted_per_units",
    "bid_per_unit",
    "ask_per_unit",
    "last_per_unit",
    "insref",
    "instrument_name",
    "last_rate",
    "bid_rate",
    "ask_rate",
]


def _safe_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_div(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def parse_handelsbanken_payload(payload):
    if not isinstance(payload, list):
        raise ValueError("Expected Handelsbanken payload to be a list")

    rows = []

    for item in payload:
        if not isinstance(item, dict):
            continue

        name = (item.get("name") or "").strip()

        # Exempel: "EUR/SEK Spot"
        pair = name.replace(" Spot", "").strip()

        base_currency = None
        quote_currency = None

        if "/" in pair:
            base_currency, quote_currency = pair.split("/", 1)

        # Millistream's `factor` is the quote multiplier — e.g. JPY is
        # typically quoted as SEK per 100 JPY (factor=100), most others as
        # SEK per 1 unit (factor=1). The per-unit rate is rate / factor.
        factor_raw = _safe_float(item.get("factor"))
        factor = int(factor_raw) if factor_raw and factor_raw == int(factor_raw) else 1

        last_rate = _safe_float(item.get("lastprice"))
        bid_rate = _safe_float(item.get("bidprice"))
        ask_rate = _safe_float(item.get("askprice"))

        rows.append(
            {
                "pair": pair,
                "base_currency": base_currency,
                "quote_currency": quote_currency,
                "quoted_per_units": factor,
                "bid_per_unit": _safe_div(bid_rate, factor),
                "ask_per_unit": _safe_div(ask_rate, factor),
                "last_per_unit": _safe_div(last_rate, factor),
                "insref": item.get("insref"),
                "instrument_name": name,
                "last_rate": last_rate,
                "bid_rate": bid_rate,
                "ask_rate": ask_rate,
            }
        )

    if not rows:
        raise ValueError("No usable Handelsbanken rows found")

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
    output=None,
)
def _scrape_handelsbanken(driver: Driver, data):
    driver.get(HANDELSBANKEN_URL)

    # Millistream-svaret är ren JSON. Plocka ut råtexten ur sidan.
    text = driver.run_js("return document.body ? document.body.innerText : '';")

    if not text:
        raise RuntimeError("Handelsbanken endpoint returned empty body")

    return text


def handelsbanken_scraper():
    output_file = get_dated_output_file("handelsbanken_rates")

    text = _scrape_handelsbanken()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Could not parse Handelsbanken response as JSON: {e}"
        ) from e

    rows = parse_handelsbanken_payload(payload)

    write_rows_to_csv(rows, output_file)

    print(f"Handelsbanken rates saved to {output_file}")

    return output_file


if __name__ == "__main__":
    handelsbanken_scraper()