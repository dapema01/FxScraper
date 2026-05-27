from scrapers import (
    dnb_scraper,
    nordea_scraper,
    danske_bank_scraper,
    seb_scraper,
    handelsbanken_scraper,
)


def main():
    scrapers = [
        dnb_scraper,
        nordea_scraper,
        danske_bank_scraper,
        seb_scraper,
        handelsbanken_scraper,
    ]

    for scraper in scrapers:
        try:
            output_file = scraper()
            print(f"Finished: {scraper.__name__} -> {output_file}")
        except Exception as e:
            print(f"Failed: {scraper.__name__} -> {e}")


if __name__ == "__main__":
    main()