"""Plot FX rates over time from the rolling all_banks_*.csv file.

Opens an interactive matplotlib window showing each bank's mid rate for a
chosen currency pair over time (x-axis = scraped_at).

Usage:
    python plot_rates.py                # defaults to USD/SEK
    python plot_rates.py EUR            # plot EUR/SEK
    python plot_rates.py JPY/SEK        # full pair also accepted
    python plot_rates.py --list         # list available currencies and exit

Requires: pandas, matplotlib
    pip install pandas matplotlib
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


OUTPUT_DIR = Path(__file__).resolve().parent / "output"
ALL_BANKS_GLOB = "all_banks_*.csv"


def find_latest_all_banks(output_dir: Path) -> Path:
    candidates = sorted(output_dir.glob(ALL_BANKS_GLOB))
    if not candidates:
        raise FileNotFoundError(
            f"No {ALL_BANKS_GLOB} found in {output_dir}. "
            "Run main.py first to generate data."
        )
    # ISO dates sort lexically; the last one is the most recent.
    return candidates[-1]


def normalize_pair(arg: str) -> str:
    """Accept 'EUR', 'eur', 'EUR/SEK' and return a canonical 'EUR/SEK'."""
    arg = arg.strip().upper()
    if "/" in arg:
        return arg
    return f"{arg}/SEK"


def load_data(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["scraped_at"] = pd.to_datetime(df["scraped_at"], errors="coerce")
    df = df.dropna(subset=["scraped_at"])
    return df


def plot_pair(df: pd.DataFrame, pair: str) -> None:
    subset = df[df["pair"] == pair].copy()
    if subset.empty:
        available = ", ".join(sorted(df["pair"].dropna().unique()))
        raise ValueError(
            f"No data for pair '{pair}'.\nAvailable pairs: {available}"
        )

    # One line per bank: mid rate over time. Sort so lines connect in order.
    subset = subset.sort_values("scraped_at")

    fig, ax = plt.subplots(figsize=(11, 6))

    for bank, group in subset.groupby("bank"):
        group = group.dropna(subset=["mid"])
        if group.empty:
            continue
        ax.plot(
            group["scraped_at"],
            group["mid"],
            marker="o",
            markersize=3,
            linewidth=1.5,
            label=bank,
        )

    ax.set_title(f"{pair} — mid rate over time, by bank")
    ax.set_xlabel("Scraped at (UTC)")
    ax.set_ylabel(f"{pair.split('/')[1]} per 1 {pair.split('/')[0]}")
    ax.legend(title="Bank")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()

    plt.show()


def main(argv: list[str]) -> None:
    path = find_latest_all_banks(OUTPUT_DIR)
    df = load_data(path)

    if "--list" in argv:
        pairs = sorted(df["pair"].dropna().unique())
        print(f"Data file: {path.name}")
        print(f"Available pairs ({len(pairs)}):")
        for p in pairs:
            print(f"  {p}")
        return

    # First non-flag arg is the pair; default USD/SEK.
    pair_args = [a for a in argv if not a.startswith("-")]
    pair = normalize_pair(pair_args[0]) if pair_args else "USD/SEK"

    print(f"Plotting {pair} from {path.name} ...")
    plot_pair(df, pair)


if __name__ == "__main__":
    main(sys.argv[1:])