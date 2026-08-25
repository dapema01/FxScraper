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
    "pair",
    "base_currency",
    "quote_currency",
    "quoted_per_units",
    "bid_per_unit",
    "ask_per_unit",
    "buy_rate",
    "sell_rate",
    "date",
]


# SEB returnerar {"headers": [...], "rows": [...]} där varje rads "data"-lista
# ligger i samma ordning som headers. Ordningen HAR ändrats en gång (Land och
# Valuta bytte plats i augusti 2026), vilket inte kastade något fel utan bara
# gjorde base_currency till ett landsnamn. Därför slår vi upp positionerna via
# headers i stället för att hårdkoda dem. Både svenska och engelska etiketter
# accepteras eftersom endpointen verkar variera med språk.
HEADER_ALIASES = {
    "currency": "currency",   "valuta": "currency",
    "country": "country",     "land": "country",
    "seb buy": "buy_rate",    "seb kop": "buy_rate",   "kopkurs": "buy_rate",
    "seb sell": "sell_rate",  "seb salj": "sell_rate", "saljkurs": "sell_rate",
    "date": "date",           "datum": "date",
}

REQUIRED_COLUMNS = ("currency", "buy_rate", "sell_rate")


def _norm_header(text):
    return (
        (text or "")
        .strip()
        .lower()
        .replace("\xa0", " ")
        .replace("ö", "o")
        .replace("ä", "a")
        .replace("å", "a")
    )


def build_column_index(headers):
    """Mappa logiskt kolumnnamn -> position utifrån payloadens headers.

    Kastar hellre än gissar: byter SEB namn på en kolumn vill vi ha ett hårt
    fel i CI, inte rader där base_currency tyst blivit något annat.
    """
    idx = {}

    for i, header in enumerate(headers or []):
        if not isinstance(header, dict):
            continue

        key = HEADER_ALIASES.get(_norm_header(header.get("value")))
        if key and key not in idx:
            idx[key] = i

    missing = [c for c in REQUIRED_COLUMNS if c not in idx]
    if missing:
        found = [h.get("value") for h in (headers or []) if isinstance(h, dict)]
        raise ValueError(
            f"SEB-payloaden saknar kolumn(er): {', '.join(missing)}. "
            f"Headers i svaret: {found}"
        )

    return idx


def _cell_value(row_data, index):
    if index is None or index >= len(row_data):
        return None

    cell = row_data[index]
    if not isinstance(cell, dict):
        return None

    return cell.get("value")


def parse_number(value):
    """SEB noterar med svenskt decimaltecken och kan ha tusentalsavskiljare."""
    if value is None:
        return None

    text = str(value).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not text:
        return None

    try:
        return float(text)
    except ValueError:
        return None


def parse_seb_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected SEB payload to be a dict with headers/rows")

    column_index = build_column_index(payload.get("headers"))

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

        currency = (_cell_value(data, column_index["currency"]) or "").strip().upper()

        # En valutakod är tre bokstäver. Filtrerar bort ev. rubrik-/summarader.
        if len(currency) != 3 or not currency.isalpha():
            continue

        buy_rate = parse_number(_cell_value(data, column_index["buy_rate"]))
        sell_rate = parse_number(_cell_value(data, column_index["sell_rate"]))

        parsed_rows.append(
            {
                "country": _cell_value(data, column_index.get("country")),
                "pair": f"{currency}/SEK",
                "base_currency": currency,
                "quote_currency": "SEK",
                "quoted_per_units": 1,
                "bid_per_unit": buy_rate,
                "ask_per_unit": sell_rate,
                "buy_rate": buy_rate,
                "sell_rate": sell_rate,
                "date": _cell_value(data, column_index.get("date")),
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
    output=None,
)
def _scrape_seb(driver: Driver, data):
    driver.get(SEB_URL)

    # Webbläsaren renderar JSON som text i body. Plocka ut råtexten.
    text = driver.run_js("return document.body ? document.body.innerText : '';")

    if not text:
        raise RuntimeError("SEB endpoint returned empty body")

    return text


def seb_scraper():
    text = _scrape_seb()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse SEB response as JSON: {e}") from e
    return parse_seb_payload(payload)


if __name__ == "__main__":
    output_file = get_dated_output_file("seb_rates")
    write_rows_to_csv(seb_scraper(), output_file)
    print(f"SEB rates saved to {output_file}")