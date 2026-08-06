from pathlib import Path
import pandas as pd
import plotly.graph_objects as go

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DOCS_DIR = Path(__file__).resolve().parent / "docs"
ALL_BANKS_PREFIX = "all_banks_"
ALLOWED_PAIRS = ["USD/SEK", "EUR/SEK", "GBP/SEK"]

def latest_all_banks_file(output_dir: Path) -> Path:
    candidates = sorted(output_dir.glob(f"{ALL_BANKS_PREFIX}*.csv"))
    if not candidates:
        raise FileNotFoundError(f"Ingen {ALL_BANKS_PREFIX}*.csv i {output_dir}")
    return candidates[-1]

path = latest_all_banks_file(OUTPUT_DIR)
df = pd.read_csv(path, parse_dates=["scraped_at"])
df = df[df["pair"].isin(ALLOWED_PAIRS)]
df["spread_pct"] = df["spread"] / df["mid"] * 100

fig = go.Figure()
trace_pairs = []          # vilket par varje trace tillhör
y_full, y_nowe = [], []   # y med resp. utan helger, för toggle

for pair in ALLOWED_PAIRS:
    if pair not in df["pair"].unique():
        continue
    for bank, g in df[df["pair"] == pair].sort_values("scraped_at").groupby("bank"):
        g = g.reset_index(drop=True)
        if g["spread_pct"].notna().sum() == 0:
            continue  # hoppa över banker utan spread (t.ex. Handelsbanken)

        full = g["spread_pct"].tolist()
        nowe = g["spread_pct"].where(g["weekend_flag"] == 0).tolist()

        fig.add_trace(
            go.Scatter(
                x=g["scraped_at"], y=full, mode="lines+markers", name=bank,
                visible=(pair == ALLOWED_PAIRS[0]),
                customdata=g[["bid", "ask", "mid"]].to_numpy(),
                hovertemplate=(
                    f"<b>{bank}</b><br>"
                    "%{x|%Y-%m-%d %H:%M}<br>"
                    "spread: %{y:.3f} %<br>"
                    "bid: %{customdata[0]:.4f}<br>"
                    "ask: %{customdata[1]:.4f}<br>"
                    "mid: %{customdata[2]:.4f}<extra></extra>"
                ),
            )
        )
        trace_pairs.append(pair)
        y_full.append(full)
        y_nowe.append(nowe)

# Par-dropdown: styr vilka traces som syns (visible)
pair_buttons = [
    dict(label=p, method="restyle",
         args=[{"visible": [tp == p for tp in trace_pairs]}])
    for p in ALLOWED_PAIRS if p in trace_pairs
]

# Helg-knappar: byter y-data för ALLA traces (oberoende av visible)
all_idx = list(range(len(trace_pairs)))
weekend_buttons = [
    dict(label="Visa helger", method="restyle", args=[{"y": y_full}, all_idx]),
    dict(label="Dölj helger", method="restyle", args=[{"y": y_nowe}, all_idx]),
]

fig.update_layout(
    title="FX-spread (% av mid) per bank",
    xaxis_title="Skrapningstid", yaxis_title="Spread (%)",
    hovermode="closest",
    updatemenus=[
        dict(buttons=pair_buttons, x=0.0, y=1.15, xanchor="left"),
        dict(buttons=weekend_buttons, x=0.25, y=1.15, xanchor="left"),
    ],
)
fig.update_xaxes(showgrid=True, griddash="dash")
fig.update_yaxes(showgrid=True, griddash="dash")

DOCS_DIR.mkdir(exist_ok=True)
out = DOCS_DIR / "index.html"
fig.write_html(out, include_plotlyjs="cdn")
print(f"Skrev {out}")