from csv import DictWriter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

STOCKHOLM = ZoneInfo("Europe/Stockholm")


# Tidy/long-format: en rad per (bank, valutapar, scrape).
# Den här formen är vad plottnings- och tidsserieverktyg (pandas, seaborn,
# plotly) förväntar sig. Att lägga till en ny bank ger fler rader, inte
# fler kolumner.
#
# Konventioner för kurskolumner:
#   bid/ask/mid/spread   -> normaliserade till quote_currency per 1 enhet av base_currency
#                           (jämförbara mellan banker).
#   raw_bid/raw_ask      -> per `raw_quoted_per_units` enheter av base_currency,
#                           exakt som banken publicerar. Användbart för spårbarhet
#                           och för att återskapa bankens eget tal.
#   raw_quoted_per_units -> multiplikatorn banken noterar i (1 eller 100).
UNIFIED_FIELDNAMES = [
    "scraped_at",
    "bank",
    "pair",
    "base_currency",
    "quote_currency",
    "bid",
    "ask",
    "mid",
    "spread",
    "raw_bid",
    "raw_ask",
    "raw_quoted_per_units",
    "rate_date",
    "source_bank_label",
]


@dataclass
class UnifiedRate:
    """En enskild FX-observation i projektets kanoniska form.

    Konventioner:
        - bid: kursen som banken KÖPER base_currency för från en kund
               (lägre tal).
        - ask: kursen som banken SÄLJER base_currency för till en kund
               (högre tal).
        - bid/ask/mid/spread noteras alltid som quote_currency per 1 enhet
          av base_currency. Om banken publicerade "5.82 SEK per 100 JPY"
          lagrar vi bid/ask som 0.0582 och lägger 5.82 i raw_bid/raw_ask med
          raw_quoted_per_units=100.
        - pair byggs automatiskt som f"{base_currency}/{quote_currency}" om
          den inte sätts explicit. Alla fem bankerna producerar för
          närvarande <FX>/SEK-par.
        - scraped_at är den kanoniska tidsaxeln för plottning (sätts alltid,
          alltid jämförbar mellan banker). rate_date är ett bästa-möjliga-
          värde från banken själv och kan saknas eller ha ett udda format.
    """

    scraped_at: str
    bank: str
    base_currency: str
    quote_currency: str = "SEK"
    pair: Optional[str] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    spread: Optional[float] = None
    raw_bid: Optional[float] = None
    raw_ask: Optional[float] = None
    raw_quoted_per_units: Optional[int] = None
    rate_date: Optional[str] = None
    source_bank_label: Optional[str] = None

    def __post_init__(self):
        # Bygg par-etiketten automatiskt så att adaptrarna aldrig behöver stava ut den.
        if self.pair is None and self.base_currency and self.quote_currency:
            self.pair = f"{self.base_currency}/{self.quote_currency}"

        # Fyll i mid och spread automatiskt från bid/ask när de inte angetts.
        # Avrunda till 6 decimaler — tillräcklig precision för FX-kurser, dödar float-brus.
        if self.bid is not None and self.ask is not None:
            if self.mid is None:
                self.mid = round((self.bid + self.ask) / 2, 6)
            if self.spread is None:
                self.spread = round(self.ask - self.bid, 6)


def now_iso():
    """Tidsstämpel i svensk lokal tid (Europe/Stockholm), ISO 8601 med
    sekundprecision. Hanterar sommar/vintertid automatiskt."""
    return datetime.now(STOCKHOLM).replace(microsecond=0).isoformat()


def write_unified_csv(rates, output_file: Path, append: bool = False):
    """Skriv en lista av UnifiedRate-objekt till en CSV-fil.

    Args:
        rates: itererbar av UnifiedRate-instanser.
        output_file: målsökväg. Föräldrakatalogen måste finnas.
        append: om True, lägg till och hoppa över rubriken när filen redan
                har innehåll. Om False (standard), skriv över.
    """
    output_file = Path(output_file)
    mode = "a" if append else "w"

    write_header = True
    if append and output_file.exists() and output_file.stat().st_size > 0:
        write_header = False

    with output_file.open(mode, newline="", encoding="utf-8") as f:
        writer = DictWriter(f, fieldnames=UNIFIED_FIELDNAMES)

        if write_header:
            writer.writeheader()

        for rate in rates:
            row = asdict(rate)
            # Skydda mot extra fält om dataklassen någonsin växer.
            writer.writerow({k: row.get(k) for k in UNIFIED_FIELDNAMES})

    return output_file