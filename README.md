# FX Scraper

Pythonprojekt för att hämta valutakurser från banker och spara resultatet som CSV-filer i `output/`.

Scrapers som finns just nu:

- DNB
- Nordea

## Projektstruktur

```
├── main.py
├── scrapers/
│   ├── __init__.py
│   ├── dnb.py
│   └── nordea.py
├── utils/
│   ├── __init__.py
│   └── file_utils.py
├── output/
└── README.md
```

## Köra projektet

Kör alla scrapers:

```bash
python main.py
```

Kör en enskild scraper:

```bash
python scrapers/dnb.py
python scrapers/nordea.py
```
## Lägga till en ny scraper

Skapa en ny fil i `scrapers/`, exempelvis:

```
scrapers/"swdbank".py
```

Lägg in en funktion som slutar på `_scraper`, exempelvis:

```python
from utils import get_dated_output_file

def swedbank_scraper():
    output_file = get_dated_output_file("swedbank_rates")

    # hämta data
    # bearbeta data
    # spara data

    return output_file
```

Exportera funktionen i `scrapers/__init__.py`:

```python
from .swedbank import swedbank_scraper

__all__ = [
    "swedbank_scraper",
]
```

Lägg sedan till scrapern i `main.py`:

```python
from scrapers import swedbank_scraper

scrapers = [
    swedbank_scraper,
]
```

## Utils

Gemensamma hjälpfunktioner ligger i `utils/`.

Just nu finns dessa:

```python
get_project_root()
get_output_dir()
get_dated_output_file(prefix)
```

De används för att hitta projektmappen, skapa `output/` och skapa filnamn med dagens datum.

Om du skapar en ny hjälpfunktion i `utils/`, exportera den också i `utils/__init__.py`.

## Output

Alla genererade CSV-filer sparas i `output/`.

Exempel:

```
output/dnb_rates_2026-03-27.csv
```

`output/` ska ignoreras av Git eftersom det bara innehåller genererade filer.
