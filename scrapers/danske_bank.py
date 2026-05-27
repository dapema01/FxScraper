import csv
from pathlib import Path
import sys

from botasaurus.browser import browser, Driver
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import get_dated_output_file


DANSKE_URL = (
    "https://online.danskebank.se/DK"
    "?gsProdukt=INF&gsNextObj=Valuta&gsNextAkt=VFList0S"
    "&gsSprog=SE&gsBrand=DB&FixingListegsCurItem=Init"
    "&FixingListegsCurItem2=SESEK"
)


FIELDNAMES = [
    "pair",
    "base_currency",
    "quote_currency",
    "quoted_per_units",
    "bid_per_unit",
    "ask_per_unit",
    "currency",
    "name",
    "buy_from_abroad",
    "sell_to_abroad",
    "mid_rate",
    "date",
]


def parse_danske_html(html):
    soup = BeautifulSoup(html, "html.parser")

    table = soup.select_one("#vtab")
    if table is None:
        raise ValueError("Could not find #vtab table in Danske Bank HTML")

    rows = []

    for tr in table.select("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.select("th, td")]
        cells = [c for c in cells if c]

        # En riktig datarad är 6 fält och börjar med en valutakod (3 bokstäver):
        # [USD, Amerikanska dollar, 9,3710, 9,5048, 9,4379, 2026-03-27]
        if len(cells) == 6 and len(cells[0]) == 3 and cells[0].isalpha():
            currency = cells[0]
            buy_from_abroad = cells[2].replace(",", ".")
            sell_to_abroad = cells[3].replace(",", ".")
            rows.append(
                {
                    "pair": f"{currency}/SEK",
                    "base_currency": currency,
                    "quote_currency": "SEK",
                    "quoted_per_units": 1,
                    "bid_per_unit": buy_from_abroad,
                    "ask_per_unit": sell_to_abroad,
                    "currency": currency,
                    "name": cells[1],
                    "buy_from_abroad": buy_from_abroad,
                    "sell_to_abroad": sell_to_abroad,
                    "mid_rate": cells[4].replace(",", "."),
                    "date": cells[5],
                }
            )

    if not rows:
        raise ValueError("No usable Danske Bank rows found")

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
def _scrape_danske(driver: Driver, data):
    driver.get(DANSKE_URL)

    # Vänta in att tabellen finns i DOM:en innan vi läser HTML.
    driver.wait_for_element("#vtab", wait=30)

    return driver.page_html


def danske_bank_scraper():
    output_file = get_dated_output_file("danske_bank_rates")

    html = _scrape_danske()
    if not html:
        raise RuntimeError("Danske Bank returned no HTML")

    rows = parse_danske_html(html)

    write_rows_to_csv(rows, output_file)

    print(f"Danske Bank rates saved to {output_file}")

    return output_file


if __name__ == "__main__":
    danske_bank_scraper()