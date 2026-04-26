from scrapers.dnb import dnb_scraper
from scrapers.nordea import nordea_scraper


def main():
    scrapers = [
        dnb_scraper,
        nordea_scraper,
    ]

    for scraper in scrapers:
        try:
            output_file = scraper()
            print(f"Finished: {scraper.__name__} -> {output_file}")
        except Exception as e:
            print(f"Failed: {scraper.__name__} -> {e}")


if __name__ == "__main__":
    main()