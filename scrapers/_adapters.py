"""Adaptrar som översätter varje scrapers råa rad-dictar till UnifiedRate-
objekt i projektets kanoniska form.

Varje per-bank-CSV exponerar en enhetlig uppsättning kolumner:

    - pair / base_currency / quote_currency
    - quoted_per_units  (1 eller 100; hur många enheter av base_currency
                         bankens råa kurs avser)
    - bid_per_unit / ask_per_unit  (normaliserade till quote_currency per
                                    1 enhet av base_currency)
    - bankspecifika råkolumner (behålls för spårbarhet)

Adaptrarna gör därför alla samma sak:
    bid           <- bid_per_unit
    ask           <- ask_per_unit
    raw_bid       <- bankens råa köpkurs (bid)
    raw_ask       <- bankens råa säljkurs (ask)
    raw_quoted_per_units <- bankens `quoted_per_units`-värde

Det enda per-bank-specifika som återstår är vilken kolumn som håller den
"råa" kursen (eftersom varje bank döper den olika) och hur bankens datum
normaliseras.

Handelsbanken är undantaget: deras bid/ask är interbankkurser (inte
jämförbara med kurser mot privat-/företagskund), så vi exponerar bara
`mid` i den enhetliga utdatan. Råa bid/ask bevaras fortfarande i
per-bank-CSV:n.
"""

from datetime import date, datetime
from typing import Iterable, List, Optional

from utils.unified import UnifiedRate, now_iso


def _to_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalize_dnb_date(raw: Optional[str]) -> Optional[str]:
    # DNB-format: "26.05.2026 09:00"
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%d.%m.%Y %H:%M").date().isoformat()
    except ValueError:
        return raw


def _normalize_nordea_timestamp(raw: Optional[str]) -> Optional[str]:
    # Nordea-format: "2026-05-27T07:11:22.966896Z" -> "2026-05-27"
    if not raw:
        return None
    return raw.split("T", 1)[0]


def _normalize_seb_date(raw: Optional[str], scraped_date: date) -> Optional[str]:
    # SEB-format: "27/5" (dag/månad, inget år). Anta scrapeårets år.
    #
    # Specialfall vid årsskiftet: dag/månad utan år blir tvetydigt. Om vi
    # fyller i scrapeårets år och datumet då hamnar i framtiden (senare än
    # scrapedagen, dvs imorgon eller längre fram) kan det inte vara dagens
    # publicerade kurs — då rör det sig om förra årets notering (t.ex.
    # "31/12" som lästs in när vi scrapar i januari). I så fall drar vi av
    # ett år.
    if not raw:
        return None
    try:
        day, month = raw.split("/", 1)
        day, month = int(day), int(month)
    except (ValueError, AttributeError):
        return raw

    try:
        candidate = date(scraped_date.year, month, day)
    except ValueError:
        return raw

    if candidate > scraped_date:
        try:
            candidate = candidate.replace(year=scraped_date.year - 1)
        except ValueError:
            # 29/2 i scrapeåret men inte föregående år.
            candidate = date(scraped_date.year - 1, month, 28)

    return candidate.isoformat()


def _normalize_swedbank_date(raw: Optional[str]) -> Optional[str]:
    # Swedbank-format: redan ISO i caption, t.ex. "2026-06-02".
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw


def from_dnb_rows(raw_rows: Iterable[dict], scraped_at: Optional[str] = None) -> List[UnifiedRate]:
    scraped_at = scraped_at or now_iso()
    out = []

    for row in raw_rows:
        base = row.get("base_currency") or row.get("currency")
        if not base:
            continue

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="DNB",
                base_currency=base,
                quote_currency=row.get("quote_currency") or "SEK",
                bid=_to_float(row.get("bid_per_unit")),
                ask=_to_float(row.get("ask_per_unit")),
                raw_bid=_to_float(row.get("buy_rate")),
                raw_ask=_to_float(row.get("sell_rate")),
                raw_quoted_per_units=_to_int(row.get("quoted_per_units")),
                rate_date=_normalize_dnb_date(row.get("updated")),
                source_bank_label=row.get("country"),
            )
        )

    return out


def from_nordea_rows(raw_rows: Iterable[dict], scraped_at: Optional[str] = None) -> List[UnifiedRate]:
    scraped_at = scraped_at or now_iso()
    out = []

    for row in raw_rows:
        base = row.get("base_currency")
        if not base:
            continue

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="Nordea",
                base_currency=base,
                quote_currency=row.get("quote_currency") or "SEK",
                bid=_to_float(row.get("bid_per_unit")),
                ask=_to_float(row.get("ask_per_unit")),
                raw_bid=_to_float(row.get("bid_sek_per_fx")),
                raw_ask=_to_float(row.get("ask_sek_per_fx")),
                raw_quoted_per_units=_to_int(row.get("quoted_per_units")),
                rate_date=_normalize_nordea_timestamp(row.get("ask_timestamp")),
            )
        )

    return out


def from_seb_rows(raw_rows: Iterable[dict], scraped_at: Optional[str] = None) -> List[UnifiedRate]:
    scraped_at = scraped_at or now_iso()
    scrape_date = datetime.fromisoformat(scraped_at.replace("Z", "+00:00")).date()
    out = []

    for row in raw_rows:
        base = row.get("base_currency") or row.get("currency")
        if not base:
            continue

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="SEB",
                base_currency=base,
                quote_currency=row.get("quote_currency") or "SEK",
                bid=_to_float(row.get("bid_per_unit")),
                ask=_to_float(row.get("ask_per_unit")),
                raw_bid=_to_float(row.get("buy_rate")),
                raw_ask=_to_float(row.get("sell_rate")),
                raw_quoted_per_units=_to_int(row.get("quoted_per_units")),
                rate_date=_normalize_seb_date(row.get("date"), scrape_date),
                source_bank_label=row.get("country"),
            )
        )

    return out


def from_swedbank_rows(raw_rows: Iterable[dict], scraped_at: Optional[str] = None) -> List[UnifiedRate]:
    scraped_at = scraped_at or now_iso()
    out = []

    for row in raw_rows:
        base = row.get("base_currency") or row.get("currency")
        if not base:
            continue

        # Swedbank noterar per 1 enhet, så råkurserna är redan per enhet.
        quoted_per_units = _to_int(row.get("quoted_per_units")) or 1
        bid = _to_float(row.get("sell_rate"))
        ask = _to_float(row.get("buy_rate"))

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="Swedbank",
                base_currency=base,
                quote_currency=row.get("quote_currency") or "SEK",
                bid=bid,
                ask=ask,
                raw_bid=bid,
                raw_ask=ask,
                raw_quoted_per_units=quoted_per_units,
                rate_date=_normalize_swedbank_date(row.get("updated")),
                source_bank_label=row.get("country"),
            )
        )

    return out


def from_danske_bank_rows(raw_rows: Iterable[dict], scraped_at: Optional[str] = None) -> List[UnifiedRate]:
    scraped_at = scraped_at or now_iso()
    out = []

    for row in raw_rows:
        base = row.get("base_currency") or row.get("currency")
        if not base:
            continue

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="Danske Bank",
                base_currency=base,
                quote_currency=row.get("quote_currency") or "SEK",
                bid=_to_float(row.get("bid_per_unit")),
                ask=_to_float(row.get("ask_per_unit")),
                mid=_to_float(row.get("mid_rate")),
                raw_bid=_to_float(row.get("buy_from_abroad")),
                raw_ask=_to_float(row.get("sell_to_abroad")),
                raw_quoted_per_units=_to_int(row.get("quoted_per_units")),
                rate_date=row.get("date"),
                source_bank_label=row.get("name"),
            )
        )

    return out


def from_handelsbanken_rows(raw_rows: Iterable[dict], scraped_at: Optional[str] = None) -> List[UnifiedRate]:
    """Adaptera Handelsbankens råa rader till det enhetliga schemat.

    Handelsbankens Millistream-flöde är en interbank-/grossistkurs
    (~0,02 % spread), inte en kurs mot privat-/företagskund som de andra
    bankerna. Att lägga deras bid/ask bredvid DNB:s, Nordeas osv. i den
    enhetliga filen vore vilseledande — talen är inte jämförbara.

    I stället exponerar vi bara mittpunkten här (med `last_per_unit`, som
    är Handelsbankens officiella mid normaliserad till per 1 enhet; faller
    tillbaka på (bid_per_unit + ask_per_unit) / 2 om last saknas). Råa
    bid/ask bevaras fortfarande i per-bank-CSV:n.
    """
    scraped_at = scraped_at or now_iso()
    out = []

    for row in raw_rows:
        base = row.get("base_currency")
        quote = row.get("quote_currency")
        # Hoppa över par som inte är SEK-noterade om sådana skulle smyga sig in.
        if not base or quote != "SEK":
            continue

        last_per_unit = _to_float(row.get("last_per_unit"))
        bid_per_unit = _to_float(row.get("bid_per_unit"))
        ask_per_unit = _to_float(row.get("ask_per_unit"))

        if last_per_unit is not None:
            mid = last_per_unit
        elif bid_per_unit is not None and ask_per_unit is not None:
            mid = round((bid_per_unit + ask_per_unit) / 2, 6)
        else:
            mid = None

        if mid is None:
            continue

        out.append(
            UnifiedRate(
                scraped_at=scraped_at,
                bank="Handelsbanken",
                base_currency=base,
                quote_currency=quote,
                bid=None,
                ask=None,
                mid=mid,
                spread=None,
                raw_bid=_to_float(row.get("bid_rate")),
                raw_ask=_to_float(row.get("ask_rate")),
                raw_quoted_per_units=_to_int(row.get("quoted_per_units")),
                source_bank_label=row.get("instrument_name"),
            )
        )

    return out