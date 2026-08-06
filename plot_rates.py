from pathlib import Path
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import mplcursors
import pandas as pd
from matplotlib.widgets import CheckButtons, RadioButtons

OUTPUT_DIR = Path(__file__).resolve().parent / "output"  # justera vid behov
ALL_BANKS_PREFIX = "all_banks_"
ALLOWED_PAIRS = ["USD/SEK", "EUR/SEK", "GBP/SEK"]

def latest_all_banks_file(output_dir: Path) -> Path:
    candidates = sorted(output_dir.glob(f"{ALL_BANKS_PREFIX}*.csv"))
    if not candidates:
        raise FileNotFoundError(f"Ingen {ALL_BANKS_PREFIX}*.csv i {output_dir}")
    return candidates[-1]

path = latest_all_banks_file(OUTPUT_DIR)
print(f"Ritar {path.name}")
df = pd.read_csv(path, parse_dates=["scraped_at"])

df = df[df["pair"].isin(ALLOWED_PAIRS)]
df["spread_pct"] = df["spread"] / df["mid"] * 100

pairs = [p for p in ALLOWED_PAIRS if p in df["pair"].unique()]
state = {"pair": pairs[0], "hide_weekends": False, "cursor": None}

fig, ax = plt.subplots(figsize=(11, 6))
plt.subplots_adjust(left=0.28, right=0.98)

def redraw():
    if state["cursor"] is not None:
        state["cursor"].remove()

    ax.clear()
    # Streckat rutnät i bakgrunden (bakom datapunkterna)
    ax.grid(True, linestyle="--", linewidth=0.6, alpha=0.5)
    ax.set_axisbelow(True)

    sub = df[df["pair"] == state["pair"]].sort_values("scraped_at")
    scatters = []
    for bank, g in sub.groupby("bank"):
        g = g.reset_index(drop=True)  # positionsindex matchar scatterns punkter
        y = g["spread_pct"]
        if state["hide_weekends"]:
            y = y.where(g["weekend_flag"] == 0)
        if y.notna().sum() == 0:
            continue  # hoppa över banker utan spread (t.ex. Handelsbanken)

        (line,) = ax.plot(g["scraped_at"], y, linewidth=1)
        sc = ax.scatter(g["scraped_at"], y, s=30, color=line.get_color(), label=bank)
        sc.bank_label = bank
        sc.rows = g  # spara raderna för uppslag i tooltip
        scatters.append(sc)

    ax.set_title(f"{state['pair']} — spread (% av mid)")
    ax.set_xlabel("Skrapningstid")
    ax.set_ylabel("Spread (%)")
    if scatters:
        ax.legend(loc="upper left")
    fig.autofmt_xdate()

    cursor = mplcursors.cursor(scatters, hover=True)

    @cursor.connect("add")
    def _(sel):
        row = sel.artist.rows.iloc[sel.index]
        t = mdates.num2date(sel.target[0])
        sel.annotation.set_text(
            f"{sel.artist.bank_label}\n"
            f"{t:%Y-%m-%d %H:%M}\n"
            f"spread: {sel.target[1]:.3f} %\n"
            f"bid: {row['bid']:.4f}\n"
            f"ask: {row['ask']:.4f}\n"
            f"mid: {row['mid']:.4f}"
        )
        sel.annotation.get_bbox_patch().set(alpha=0.9)

    state["cursor"] = cursor
    fig.canvas.draw_idle()

# Par-väljare
ax_pair = plt.axes([0.02, 0.45, 0.22, 0.45])
radio = RadioButtons(ax_pair, pairs, active=0)
def on_pair(label):
    state["pair"] = label
    redraw()
radio.on_clicked(on_pair)

# Helg-toggle
ax_check = plt.axes([0.02, 0.30, 0.22, 0.10])
check = CheckButtons(ax_check, ["Dölj helger"], [False])
def on_check(_label):
    state["hide_weekends"] = check.get_status()[0]
    redraw()
check.on_clicked(on_check)

redraw()
plt.show()