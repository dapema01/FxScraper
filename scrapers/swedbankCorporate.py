import csv
from pathlib import Path
import sys

from botasaurus.browser import browser, Driver
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import get_dated_output_file


SWEDBANK_URL = (
    "https://www.swedbank.se/foretag/rantor-priser-och-kurser/valutakurser-betalningar.html"
)


FIELDNAMES = [
    "country",
    "currency_name",
    "currency",
    "sell_rate",
    "buy_rate",
    "updated",
]


@browser(headless=True, block_images=True, output=None)
def _fetch_swedbank_html(driver: Driver, url):
    # google_get applies Botasaurus' anti-detection navigation.
    driver.google_get(url)
    driver.wait_for_element("[data-component='valuta'] table", wait=20)
    return driver.page_html


def fetch_html(url):
    try:
        html = _fetch_swedbank_html(url)
    except Exception as e:
        raise RuntimeError(f"Failed while fetching Swedbank rates: {e}") from e

    if not html:
        raise RuntimeError("Swedbank returned an empty page")

    return html


def parse_float(value, default=None):
    value = value.strip().replace("\xa0", "").replace(" ", "")

    if not value:
        return default

    # Swedish decimals use comma, and thousands may use a separator.
    value = value.replace(".", "").replace(",", ".")

    try:
        return float(value)
    except ValueError:
        return default


def find_rate_table(soup):
    valuta_section = soup.find(attrs={"data-component": "valuta"})
    if valuta_section is None:
        raise ValueError(
            "Could not find Swedbank valuta section (data-component='valuta')"
        )

    table = valuta_section.find("table")
    if table is None:
        raise ValueError("Found valuta section but no table inside it")

    return table


def extract_updated(table):
    caption = table.find("caption")
    if caption is None:
        return None

    text = caption.get_text(strip=True)

    # Caption looks like:
    # "Valutakurser betalningar, senast uppdaterad 2026-06-02"
    marker = "senast uppdaterad"
    if marker in text:
        return text.split(marker, 1)[1].strip()

    return text or None


def parse_swedbank_html(text):
    soup = BeautifulSoup(text, "html.parser")
    table = find_rate_table(soup)
    updated = extract_updated(table)

    body = table.find("tbody") or table
    rows = []

    for tr in body.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 5:
            continue

        values = [cell.get_text(strip=True) for cell in cells]
        country, currency_name, currency, sell_rate, buy_rate = values[:5]

        if not currency:
            continue

        rows.append(
            {
                "country": country,
                "currency_name": currency_name,
                "currency": currency,
                "sell_rate": parse_float(sell_rate),
                "buy_rate": parse_float(buy_rate),
                "updated": updated,
            }
        )

    if not rows:
        raise ValueError("No usable Swedbank rows found")

    return rows


def write_rows_to_csv(rows, output_file):
    with output_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def swedbank_scraper():
    output_file = get_dated_output_file("swedbank_rates")

    text = fetch_html(SWEDBANK_URL)
    rows = parse_swedbank_html(text)

    write_rows_to_csv(rows, output_file)

    print(f"Swedbank rates saved to {output_file}")

    return output_file


if __name__ == "__main__":
    swedbank_scraper()