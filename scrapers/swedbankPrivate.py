"""Swedbank PRIVAT-scraper.

Återanvänder företags-scraperns parsnings- och hämtningslogik rakt av
(se scrapers/swdbank.py). Det enda som skiljer privat från företag är
URL:en och output-prefixet — Swedbank är den enda banken vars publicerade
kurser faktiskt skiljer sig mellan privat- och företagskund (ett litet
påslag på ~0,03–0,05 %), så den måste scrapas separat.

Allt annat (HTML-struktur, kolumner, decimalhantering, caption-datum) är
identiskt med företagssidan, så vi importerar de funktionerna istället för
att duplicera dem.
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import get_dated_output_file

# Återanvänd företags-scraperns hämtning + parsning oförändrad.
from scrapers.swedbankCorporate import fetch_html, parse_swedbank_html, write_rows_to_csv


SWEDBANK_PRIVATE_URL = (
    "https://www.swedbank.se/privat/rantor-priser-och-kurser/valutakurser-betalningar.html"
)


def swedbank_private_scraper():
    output_file = get_dated_output_file("swedbank_private_rates")

    text = fetch_html(SWEDBANK_PRIVATE_URL)
    rows = parse_swedbank_html(text)

    write_rows_to_csv(rows, output_file)

    print(f"Swedbank PRIVATE rates saved to {output_file}")

    return output_file


if __name__ == "__main__":
    swedbank_private_scraper()