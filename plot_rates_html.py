import json
import math
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import plotly.graph_objects as go

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DOCS_DIR = Path(__file__).resolve().parent / "docs"
ALL_BANKS_PREFIX = "all_banks_"
ALLOWED_PAIRS = ["USD/SEK", "EUR/SEK", "GBP/SEK", "NOK/SEK", "DKK/SEK"]
PALETTE = ["#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A",
           "#19D3F3", "#FF6692", "#B6E880", "#FF97FF", "#FECB52"]
OUTLIER_THRESHOLD = 0.10   # >10 % avvikelse fran lokal median = spike
SWEDBANK_BASELINE = "Swedbank"
SWEDBANK_FALLBACK = "Swedbank (Privat)"

# Tillbakablicksfonstret ar en slider och kan darfor inte forberaknas.
# Delta-, trend- och jamforelsekolumnerna samt grafens trendlinje raknas
# i stallet om i webblasaren vid varje sliderdrag; Python skickar ut
# raserierna en gang.
MIN_LOOKBACK = 3
DEFAULT_LOOKBACK = 30

# En pip = minsta noterade prissteget i quote-valutan. Alla par har ar
# <FX>/SEK och noteras med fyra decimaler, sa 1 pip = 0,0001 SEK.
# Lagg in ett undantag har om ett par nagon gang noteras med tva decimaler
# (klassiskt JPY-fall), t.ex. {"JPY/SEK": 0.01}.
DEFAULT_PIP_SIZE = 0.0001
PIP_SIZE_OVERRIDES = {}

UNITS = ["pct", "pips"]          # % av mid / pips (absolut spread)
VIEWS = ["bank", "all"]          # bankdagar / alla dagar
OUTLIER_MODES = ["raw", "clean"] # visa spikar / rensa spikar

# Snittlinjen: dagligt medelvarde over "jamforelsegruppen", dvs alla banker
# utom Swedbank (foretag + privat). Swedbank ar tabellens jamforelsebank och
# ska darfor inte inga i det snitt den mats mot - annars jamfors den delvis
# med sig sjalv. Handelsbanken faller bort automatiskt eftersom deras
# interbankflode inte ger nagon jamforbar spread.
#
# Bandet ar +/-MEAN_BAND runt snittet, alltsa ett relativt band: dagar med
# bred spread far ett brett band. MEAN_MIN_BANKS nollar ut dagar dar for fa
# banker rapporterat for att ett snitt ska betyda nagot.
MEAN_EXCLUDE = {"Swedbank", "Swedbank (Privat)"}
MEAN_BAND = 0.10
MEAN_MIN_BANKS = 2

# Saknas en bank en enskild dag skulle snittet den dagen rakna pa en annan
# uppsattning banker an dagarna omkring - och eftersom bankerna ligger pa
# olika nivaer blir det ett hack i linjen som ser ut som en marknadsrorelse
# men bara ar ett byte av deltagarlista. Darfor skrivs varje banks EGEN
# senast kanda dagsniva fram over hal, sa att snittet alltid raknas pa samma
# panel. Ingen framskrivning sker fore bankens forsta observation.
#
# Limiten raknas i dagar dar NAGON bank rapporterade (helger och tomma dagar
# ater alltsa inte upp budgeten). Satt 0 for att sla av framskrivningen helt
# och None for obegransad.
MEAN_FFILL_DAYS = 3
MEAN_LINE_COLOR = "#2f3640"
MEAN_FILL_COLOR = "rgba(47, 54, 64, 0.13)"


def pip_size(pair: str) -> float:
    return PIP_SIZE_OVERRIDES.get(pair, DEFAULT_PIP_SIZE)


def latest_all_banks_file(output_dir: Path) -> Path:
    candidates = sorted(output_dir.glob(f"{ALL_BANKS_PREFIX}*.csv"))
    if not candidates:
        raise FileNotFoundError(f"Ingen {ALL_BANKS_PREFIX}*.csv i {output_dir}")
    return candidates[-1]


def clean(vals):
    """NaN -> None sa JSON blir giltig och Plotly ser dem som gap."""
    out = []
    for v in vals:
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            out.append(None)
        else:
            out.append(float(v))
    return out


# ---------------------------------------------------------------- data ----

path = latest_all_banks_file(OUTPUT_DIR)
df = pd.read_csv(path)

# Blandade UTC-offsets (sommar-/vintertid) i scraped_at: las som UTC och
# konvertera till svensk tid, sa att .dt-accessorn alltid fungerar.
df["scraped_at"] = pd.to_datetime(df["scraped_at"], utc=True).dt.tz_convert("Europe/Stockholm")

df = df[df["pair"].isin(ALLOWED_PAIRS)].copy()
df = df.sort_values("scraped_at")

# Tva matt pa samma spread:
#   spread_pct  = andel av mid, jamforbart mellan par och over tid.
#   spread_pips = absolut spread i prissteg (SEK per enhet / pipstorlek),
#                 dvs det matt en FX-desk faktiskt pratar i.
df["spread_pct"] = df["spread"] / df["mid"] * 100
df["spread_pips"] = df["spread"] / df["pair"].map(pip_size)

# Naiv, normaliserad kalenderdag - anvands for bankdagslogik och uppslag bakat.
df["day"] = df["scraped_at"].dt.tz_localize(None).dt.normalize()

xsto = xcals.get_calendar("XSTO")

_session_cache = {}


def is_session(day) -> bool:
    """Cachead bankdagskoll (XSTO) pa en naiv, normaliserad Timestamp."""
    key = pd.Timestamp(day).normalize()
    if key not in _session_cache:
        try:
            _session_cache[key] = bool(xsto.is_session(key))
        except Exception:
            # Utanfor kalenderns giltighetsintervall: falla tillbaka pa vardag.
            _session_cache[key] = key.weekday() < 5
    return _session_cache[key]


def keep_mask(g):
    return g["day"].map(is_session).astype(bool)


def detect_outliers(series, mask, threshold=OUTLIER_THRESHOLD):
    """True for isolerade spikar. Baslinjen byggs ENBART fran bankdagar, sa en
    spike dagen fore/efter en helg jamfors mot narliggande bankdagar och inte
    mot ett avvikande helgvarde som annars drar upp den lokala medianen.

    Testet kors pa den relativa spreaden och masken ateranvands for bada
    enheterna, sa att "Rensa" plockar bort exakt samma punkter oavsett om
    du tittar i procent eller pips.
    """
    baseline = series.where(mask)                     # helg/rod dag -> NaN
    med = baseline.rolling(5, center=True, min_periods=2).median()
    med = med.ffill().bfill()                         # fyll fonster utan bankdagar
    dev = (series - med).abs() / med.abs()
    dev = dev.replace([np.inf, -np.inf], np.nan)
    return (dev > threshold).fillna(False)


# Axelbrott for bankdagsvyn + bankdagskalendern som JS behover for att
# backa maldagen till narmaste session.
naive = df["scraped_at"].dt.tz_localize(None)
first_day = naive.min().normalize()
last_day = naive.max().normalize()
all_days = pd.date_range(first_day, last_day, freq="D")

holiday_breaks = [
    d.strftime("%Y-%m-%d")
    for d in all_days
    if d.weekday() < 5 and not is_session(d)
]
bankday_breaks = [dict(bounds=["sat", "mon"])]
if holiday_breaks:
    bankday_breaks.append(dict(values=holiday_breaks))

session_days = [d.strftime("%Y-%m-%d") for d in all_days if is_session(d)]
day_range = [first_day.strftime("%Y-%m-%d"), last_day.strftime("%Y-%m-%d")]

# Sliderns tak: hela den historik vi faktiskt har.
max_lookback = max(MIN_LOOKBACK + 1, int((last_day - first_day).days))
default_lookback = min(DEFAULT_LOOKBACK, max_lookback)

banks = sorted(df["bank"].unique())
bank_color = {b: PALETTE[i % len(PALETTE)] for i, b in enumerate(banks)}
present_pairs = [p for p in ALLOWED_PAIRS if p in df["pair"].unique()]


# ------------------------------------------------- serier per bank/par ----

# En enda gang per (par, bank): sortera, maska bankdagar, hitta spikar.
# Bade grafen och tabellen laser ur den har cachen, sa att siffrorna i
# tabellen alltid ar exakt de punkter som ritas i den valda vyn.
series_cache = {}

for pair in present_pairs:
    for bank, g in df[df["pair"] == pair].groupby("bank"):
        g = g.sort_values("scraped_at").reset_index(drop=True)
        if g["spread_pct"].notna().sum() == 0:
            continue  # t.ex. Handelsbanken: bara mid, ingen jamforbar spread

        km = keep_mask(g)
        spikes = detect_outliers(g["spread_pct"], km)

        c = {"g": g, "km": km}
        for unit, s in (("pct", g["spread_pct"]), ("pips", g["spread_pips"])):
            cleaned = s.where(~spikes)
            c[(unit, "all", "raw")] = s
            c[(unit, "all", "clean")] = cleaned
            c[(unit, "bank", "raw")] = s.where(km)
            c[(unit, "bank", "clean")] = cleaned.where(km)

        series_cache[(pair, bank)] = c

table_banks = {
    pair: sorted({b for (p, b) in series_cache if p == pair})
    for pair in present_pairs
}


def baseline_bank(pair):
    have = table_banks[pair]
    if SWEDBANK_BASELINE in have:
        return SWEDBANK_BASELINE
    if SWEDBANK_FALLBACK in have:
        return SWEDBANK_FALLBACK
    return None


# ------------------------------------------- dagligt snitt over banker ----

COMBOS = [(u, v, m) for u in UNITS for v in VIEWS for m in OUTLIER_MODES]


def _combo_col(unit, view, mode):
    return f"{unit}|{view}|{mode}"


def daily_mean(pair):
    """Snitt per kalenderdag over bankerna i jamforelsegruppen.

    Tva steg: forst medel per (dag, bank), sedan medel over banker. En bank
    som scrapas flera ganger samma dag far darfor inte extra vikt i snittet.

    Snittet raknas per (enhet, vy, outlier-lage) pa exakt samma maskade
    serier som ritas i grafen, sa att linjen alltid beskriver de punkter
    du faktiskt ser: i bankdagsvyn ingar inga helgvarden, och med "Rensa"
    ar spikarna borta ur snittet ocksa.
    """
    banks_in = [b for b in table_banks[pair] if b not in MEAN_EXCLUDE]
    if not banks_in:
        return None

    parts = []
    for bank in banks_in:
        c = series_cache[(pair, bank)]
        g = c["g"]
        d = pd.DataFrame({
            "day": g["day"].to_numpy(),
            "bank": bank,
            "ts_ns": g["scraped_at"].map(lambda t: t.value).to_numpy(),
        })
        for unit, view, mode in COMBOS:
            d[_combo_col(unit, view, mode)] = c[(unit, view, mode)].to_numpy()
        parts.append(d)

    long = pd.concat(parts, ignore_index=True)
    cols = [_combo_col(u, v, m) for u, v, m in COMBOS]

    per_bank = long.groupby(["day", "bank"], sort=True)[cols].mean()
    days = per_bank.index.get_level_values("day").unique().sort_values()
    bank_cols = pd.Index(banks_in, name="bank")

    daily = pd.DataFrame(index=days, columns=cols, dtype=float)
    used = pd.DataFrame(0, index=days, columns=cols, dtype=int)   # banker i snittet
    fresh = pd.DataFrame(0, index=days, columns=cols, dtype=int)  # varav egen notering

    for col in cols:
        # Panel dag x bank for den har kombinationen (enhet/vy/outlier-lage).
        mat = per_bank[col].unstack("bank").reindex(index=days, columns=bank_cols)

        # Dagar dar ingen bank alls har ett varde (helg i bankdagsvyn, hela
        # scrapen missad) lyfts ur fore framskrivningen: de ska inte fa ett
        # pahittat snitt och ska inte heller rakna av ffill-budgeten. De
        # ligger kvar som hal i serien och overbryggas av connectgaps.
        active = mat.notna().any(axis=1)
        filled = mat.loc[active]
        if MEAN_FFILL_DAYS != 0:
            filled = filled.ffill(limit=MEAN_FFILL_DAYS)
        filled = filled.reindex(days)

        daily[col] = filled.mean(axis=1)
        used[col] = filled.notna().sum(axis=1)
        fresh[col] = mat.notna().sum(axis=1)

    daily = daily.where(used >= MEAN_MIN_BANKS)

    if daily.notna().to_numpy().sum() == 0:
        return None

    # x-varde per dag: medeltidpunkten for dagens observationer, sa att
    # snittpunkten hamnar dar bankerna faktiskt noterades och inte pa midnatt.
    ts = long.groupby("day", sort=True)["ts_ns"].mean().reindex(daily.index)
    x = (
        pd.to_datetime(ts.to_numpy().astype("int64"), unit="ns", utc=True)
        .tz_convert("Europe/Stockholm")
        .strftime("%Y-%m-%d %H:%M:%S")
        .tolist()
    )

    def _counts(col):
        # [antal banker i snittet, varav med egen notering just den dagen]
        return [[int(a), int(b)] for a, b in zip(used[col], fresh[col])]

    y = {u: {v: {m: clean(daily[_combo_col(u, v, m)].tolist())
                 for m in OUTLIER_MODES} for v in VIEWS} for u in UNITS}
    n = {u: {v: {m: _counts(_combo_col(u, v, m))
                 for m in OUTLIER_MODES} for v in VIEWS} for u in UNITS}

    return {"x": x, "y": y, "n": n, "banks": banks_in}


mean_info = {}
for pair in present_pairs:
    info = daily_mean(pair)
    if info is not None:
        mean_info[pair] = info

mean_pairs = [p for p in present_pairs if p in mean_info]
mean_banks = {p: mean_info[p]["banks"] for p in mean_pairs}

meanY = {u: {v: {m: [mean_info[p]["y"][u][v][m] for p in mean_pairs]
                 for m in OUTLIER_MODES} for v in VIEWS} for u in UNITS}
meanN = {u: {v: {m: [mean_info[p]["n"][u][v][m] for p in mean_pairs]
                 for m in OUTLIER_MODES} for v in VIEWS} for u in UNITS}


# ------------------------------------------------------------- grafen -----

HOVER_UNIT = {"pct": ("spread: %{y:.3f} %", "Spread (% av mid)"),
              "pips": ("spread: %{y:.1f} pips", "Spread (pips)")}

MEAN_HOVER = {
    "pct": ("<b>Snitt banker</b><br>%{x|%Y-%m-%d}<br>"
            "spread: %{y:.3f} %<br>"
            "%{customdata[0]} banker, %{customdata[1]} med egen notering"
            "<extra></extra>"),
    "pips": ("<b>Snitt banker</b><br>%{x|%Y-%m-%d}<br>"
             "spread: %{y:.1f} pips<br>"
             "%{customdata[0]} banker, %{customdata[1]} med egen notering"
             "<extra></extra>"),
}

fig = go.Figure()
trace_pairs = []
trace_kind = []                  # "data" | "trend" | "mean" | "band"
data_idx, trend_idx = [], []
mean_idx, band_lo_idx, band_hi_idx = [], [], []

# Nastlade uppslagstabeller: [enhet][vy][outliers] -> y-array per serie.
# Ingen fonsterdimension langre - trendlinjen raknas i JS.
dataY = {u: {v: {m: [] for m in OUTLIER_MODES} for v in VIEWS} for u in UNITS}
trendOff = []
hoverT = {u: [] for u in UNITS}

# JS behover x-axeln i sekunder och kalenderdagen per punkt for att kunna
# hitta jamforelsepunkten och anpassa lutningen sjalv.
xSec, dayStr, bankOf = [], [], []
trace_pos = {}

for pair in present_pairs:
    trace_pos[pair] = {}
    for bank in table_banks[pair]:
        c = series_cache[(pair, bank)]
        g = c["g"]
        color = bank_color[bank]
        vis = (pair == present_pairs[0])

        pos = len(xSec)
        trace_pos[pair][bank] = pos
        xSec.append([float(t.timestamp()) for t in g["scraped_at"]])
        dayStr.append([d.strftime("%Y-%m-%d") for d in g["day"]])
        bankOf.append(bank)

        for unit in UNITS:
            for view in VIEWS:
                for mode in OUTLIER_MODES:
                    dataY[unit][view][mode].append(clean(c[(unit, view, mode)].tolist()))
            hoverT[unit].append(
                f"<b>{bank}</b><br>"
                "%{x|%Y-%m-%d %H:%M}<br>"
                + HOVER_UNIT[unit][0] + "<br>"
                "bid: %{customdata[0]:.4f}<br>"
                "ask: %{customdata[1]:.4f}<br>"
                "mid: %{customdata[2]:.4f}<extra></extra>"
            )
        trendOff.append([None] * len(g))

        # --- Datatrace (start: % av mid, bankdagar, ra) ---
        idx = len(fig.data)
        fig.add_trace(
            go.Scatter(
                x=g["scraped_at"], y=dataY["pct"]["bank"]["raw"][-1],
                mode="lines+markers", name=bank,
                visible=vis, connectgaps=True, legendgroup=bank,
                line=dict(color=color), marker=dict(color=color),
                customdata=g[["bid", "ask", "mid"]].to_numpy(),
                hovertemplate=hoverT["pct"][-1],
            )
        )
        trace_pairs.append(pair)
        trace_kind.append("data")
        data_idx.append(idx)

        # --- Trendtrace (start: dold), samma legendgroup som datatracen ---
        idx = len(fig.data)
        fig.add_trace(
            go.Scatter(
                x=g["scraped_at"], y=trendOff[-1], mode="lines",
                name=f"{bank} (trend)", visible=vis, showlegend=False,
                connectgaps=True, hoverinfo="skip", legendgroup=bank,
                line=dict(color=color, dash="dash", width=2),
            )
        )
        trace_pairs.append(pair)
        trace_kind.append("trend")
        trend_idx.append(idx)


# --- Snittlinje + band, tre traces per par -------------------------------
#
# Ordningen ar viktig: den undre bandkanten maste ligga direkt fore den
# ovre, eftersom fill="tonexty" fyller mot narmast foregaende SYNLIGA trace.
# Alla tre togglas ihop, sa den relationen haller.
for pair in mean_pairs:
    info = mean_info[pair]
    y0 = info["y"]["pct"]["bank"]["raw"]
    lo0 = [None if v is None else v * (1 - MEAN_BAND) for v in y0]
    hi0 = [None if v is None else v * (1 + MEAN_BAND) for v in y0]

    idx = len(fig.data)
    fig.add_trace(
        go.Scatter(
            x=info["x"], y=lo0, mode="lines", name="snitt-band-lo",
            visible=False, showlegend=False, hoverinfo="skip",
            connectgaps=True, legendgroup="__snitt__",
            line=dict(width=0, color=MEAN_LINE_COLOR),
        )
    )
    trace_pairs.append(pair)
    trace_kind.append("band")
    band_lo_idx.append(idx)

    idx = len(fig.data)
    fig.add_trace(
        go.Scatter(
            x=info["x"], y=hi0, mode="lines", name="snitt-band-hi",
            visible=False, showlegend=False, hoverinfo="skip",
            connectgaps=True, legendgroup="__snitt__",
            fill="tonexty", fillcolor=MEAN_FILL_COLOR,
            line=dict(width=0, color=MEAN_LINE_COLOR),
        )
    )
    trace_pairs.append(pair)
    trace_kind.append("band")
    band_hi_idx.append(idx)

    idx = len(fig.data)
    fig.add_trace(
        go.Scatter(
            x=info["x"], y=y0, mode="lines",
            name=f"Snitt banker +/-{int(MEAN_BAND * 100)} %",
            visible=False, showlegend=True, connectgaps=True,
            legendgroup="__snitt__",
            line=dict(color=MEAN_LINE_COLOR, dash="dash", width=2),
            customdata=info["n"]["pct"]["bank"]["raw"],
            hovertemplate=MEAN_HOVER["pct"],
        )
    )
    trace_pairs.append(pair)
    trace_kind.append("mean")
    mean_idx.append(idx)


table_baseline = {pair: (baseline_bank(pair) or "\u2013") for pair in present_pairs}
pip_sizes = {pair: pip_size(pair) for pair in present_pairs}


# --------------------------------------------------------------- layout ---

fig.update_layout(
    title=dict(text="FX-spread per bank",
               font=dict(size=26), x=0.01, xanchor="left"),
    xaxis_title="Datum", yaxis_title=HOVER_UNIT["pct"][1],
    hovermode="closest",
    margin=dict(l=60, r=20, t=70, b=50),
    legend=dict(groupclick="togglegroup"),   # klick i legenden togglar hela gruppen
)
fig.update_xaxes(showgrid=True, griddash="dash", rangebreaks=bankday_breaks)
fig.update_yaxes(showgrid=True, griddash="dash")

plot_div = fig.to_html(
    full_html=False, include_plotlyjs="cdn", div_id="fxplot",
    default_height="70vh", default_width="100%",
    config={"responsive": True},
)


def pair_btn(p, active):
    cls = ' class="active"' if active else ''
    return f'<button data-pair="{p}"{cls}>{p}</button>'


pair_buttons_html = "".join(pair_btn(p, i == 0) for i, p in enumerate(present_pairs))

PAGE = """<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<title>FX-spread</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 8px 12px; }
  .controls { display: flex; gap: 20px; margin-bottom: 6px; flex-wrap: wrap;
              align-items: center; }
  .group { display: inline-flex; border: 1px solid #ccc; border-radius: 8px; overflow: hidden; }
  .group button { border: 0; padding: 6px 14px; background: #f5f5f5; cursor: pointer; font-size: 14px; }
  .group button + button { border-left: 1px solid #ccc; }
  .group button.active { background: #636EFA; color: #fff; }
  .group button:disabled { color: #aaa; cursor: not-allowed; }
  .label { font-size: 12px; color: #666; align-self: center; margin-right: 4px; }

  .slider { display: inline-flex; align-items: center; gap: 8px; }
  .slider input[type=range] { width: 170px; accent-color: #636EFA; cursor: pointer; }
  .slider output { font-size: 14px; font-variant-numeric: tabular-nums;
                   min-width: 52px; color: #333; }

  table#statsTable { border-collapse: collapse; width: 100%; margin: 18px 0 8px;
                     font-size: 14px; font-variant-numeric: tabular-nums; }
  #statsTable caption { text-align: left; font-size: 13px; color: #555;
                        padding: 0 0 8px; }
  #statsTable th, #statsTable td { padding: 7px 10px; border-bottom: 1px solid #e6e6e6;
                                   text-align: right; white-space: nowrap; }
  #statsTable th:first-child, #statsTable td:first-child { text-align: left; }
  #statsTable thead th { border-bottom: 2px solid #ccc; color: #333; font-weight: 600;
                         background: #fafafa; }
  #statsTable tbody tr:hover { background: #f7f8ff; }
  #statsTable tr.base td { font-weight: 600; background: #f4f5ff; }
  #statsTable td.up { color: #c0392b; }     /* bredare spread = samre */
  #statsTable td.down { color: #1e8449; }   /* smalare spread = battre */
  .foot { font-size: 12px; color: #777; margin-bottom: 24px; line-height: 1.5; }
</style>
</head>
<body>
<div class="controls">
  <div><span class="label">Par:</span>
    <div class="group" id="pairGroup">__PAIR_BUTTONS__</div>
  </div>
  <div><span class="label">Enhet:</span>
    <div class="group" id="unitGroup">
      <button data-unit="pct" class="active">% av mid</button>
      <button data-unit="pips">Pips</button>
    </div>
  </div>
  <div><span class="label">Dagar:</span>
    <div class="group" id="viewGroup">
      <button data-view="bank" class="active">Bankdagar</button>
      <button data-view="all">Alla dagar</button>
    </div>
  </div>
  <div class="slider"><span class="label">F&ouml;nster:</span>
    <input type="range" id="lookbackSlider" min="__MIN_LOOKBACK__"
           max="__MAX_LOOKBACK__" value="__DEFAULT_LOOKBACK__" step="1">
    <output id="lookbackValue"></output>
  </div>
  <div><span class="label">Trend:</span>
    <div class="group" id="trendGroup">
      <button data-trend="off" class="active">Av</button>
      <button data-trend="on">P&aring;</button>
    </div>
  </div>
  <div><span class="label">Snitt &plusmn;__BAND_PCT__&nbsp;%:</span>
    <div class="group" id="meanGroup">
      <button data-mean="off" class="active">Av</button>
      <button data-mean="on">P&aring;</button>
    </div>
  </div>
  <div><span class="label">Observationer:</span>
    <div class="group" id="pointsGroup">
      <button data-points="both" class="active">Punkter + linje</button>
      <button data-points="line">Bara linje</button>
      <button data-points="off">D&ouml;lj</button>
    </div>
  </div>
  <div><span class="label">Outliers:</span>
    <div class="group" id="outlierGroup">
      <button data-outliers="raw" class="active">Visa</button>
      <button data-outliers="clean">Rensa</button>
    </div>
  </div>
</div>
__PLOT_DIV__
<table id="statsTable">
  <caption id="tableCaption"></caption>
  <thead>
    <tr>
      <th>Bank</th>
      <th id="thNow"></th>
      <th id="thPast"></th>
      <th id="thChange"></th>
      <th id="thMargin"></th>
      <th>Trend (pips/dag)</th>
      <th>Spread vs Swedbank (pips)</th>
      <th>Marginal vs Swedbank (%)</th>
    </tr>
  </thead>
  <tbody></tbody>
</table>
<div class="foot" id="tableFoot"></div>
<script>
  const Y_DATA = __Y_DATA__;
  const TREND_OFF = __TREND_OFF__;
  const X_SEC = __X_SEC__;
  const DAYS = __DAYS__;
  const BANK_OF = __BANK_OF__;
  const TRACE_POS = __TRACE_POS__;
  const HOVER_T = __HOVER_T__;
  const Y_TITLE = __Y_TITLE__;
  const DATA_IDX = __DATA_IDX__;
  const TREND_IDX = __TREND_IDX__;
  const BANKDAY_BREAKS = __BANKDAY_BREAKS__;
  const TRACE_PAIRS = __TRACE_PAIRS__;
  const TRACE_KIND = __TRACE_KIND__;
  const PRESENT_PAIRS = __PRESENT_PAIRS__;
  const TABLE_BASELINE = __TABLE_BASELINE__;
  const PIP_SIZES = __PIP_SIZES__;
  const SESSION_DAYS = __SESSION_DAYS__;
  const DAY_RANGE = __DAY_RANGE__;
  const DEFAULT_LOOKBACK = __DEFAULT_LOOKBACK__;

  const MEAN_Y = __MEAN_Y__;
  const MEAN_N = __MEAN_N__;
  const MEAN_HOVER = __MEAN_HOVER__;
  const MEAN_IDX = __MEAN_IDX__;
  const BAND_LO_IDX = __BAND_LO_IDX__;
  const BAND_HI_IDX = __BAND_HI_IDX__;
  const MEAN_PAIRS = __MEAN_PAIRS__;
  const MEAN_BANKS = __MEAN_BANKS__;
  const MEAN_BAND = __MEAN_BAND__;
  const MEAN_MIN_BANKS = __MEAN_MIN_BANKS__;
  const MEAN_FFILL_DAYS = __MEAN_FFILL_DAYS__;

  const SESSIONS = new Set(SESSION_DAYS);
  const DASH = "\\u2013";
  const UNIT_CFG = {
    pct:  { val: 3, head: "% av mid", name: "% av mid" },
    pips: { val: 1, head: "pips",     name: "pips" }
  };

  const state = { pair: PRESENT_PAIRS[0], unit: "pct", view: "bank",
                  lookback: DEFAULT_LOOKBACK, trend: false, outliers: "raw",
                  mean: false, points: "both" };

  // ------------------------------------------------------ bankdagar ----

  function shiftDay(dayStr, delta) {
    const p = dayStr.split("-");
    const d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
    d.setUTCDate(d.getUTCDate() + delta);
    return d.toISOString().slice(0, 10);
  }

  function isSession(dayStr) {
    // Innanfor exporterat intervall: exakt XSTO-kalender. Utanfor (kan handa
    // nar fonstret racker langre bak an var historik): falla tillbaka pa vardag.
    if (dayStr >= DAY_RANGE[0] && dayStr <= DAY_RANGE[1]) return SESSIONS.has(dayStr);
    const p = dayStr.split("-");
    const wd = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2])).getUTCDay();
    return wd >= 1 && wd <= 5;
  }

  function prevSessionOrSame(dayStr) {
    var d = dayStr;
    for (var k = 0; k < 15; k++) {
      if (isSession(d)) return d;
      d = shiftDay(d, -1);
    }
    return dayStr;
  }

  // ---------------------------------------------------- berakningar ----

  function validIdx(y) {
    const out = [];
    for (var k = 0; k < y.length; k++) { if (y[k] !== null) out.push(k); }
    return out;
  }

  function startIndex(days, idx, lb) {
    // Jamforelsepunkten: maldagen backas till narmaste bankdag och darefter
    // tas senaste faktiska observationen pa eller fore den dagen.
    const target = prevSessionOrSame(shiftDay(days[idx[idx.length - 1]], -lb));
    var found = -1;
    for (var k = 0; k < idx.length; k++) {
      if (days[idx[k]] <= target) { found = idx[k]; } else { break; }
    }
    return { i: found === -1 ? idx[0] : found, approx: found === -1 };
  }

  function slopePerDay(x, y, idx, from) {
    // Minsta-kvadrat-lutning per kalenderdag over fonstret.
    var n = 0, sx = 0, sy = 0, sxx = 0, sxy = 0;
    const x0 = x[from];
    for (var k = 0; k < idx.length; k++) {
      const j = idx[k];
      if (j < from) continue;
      const xv = (x[j] - x0) / 86400.0, yv = y[j];
      n++; sx += xv; sy += yv; sxx += xv * xv; sxy += xv * yv;
    }
    if (n < 2) return null;
    const den = n * sxx - sx * sx;
    if (den === 0) return null;
    return (n * sxy - sx * sy) / den;
  }

  function trendLine(pos, y, lb) {
    // Linjen ritas bara over fonstret; punkter fore jamforelsepunkten far
    // null sa att den inte extrapoleras bakat over historik den inte beskriver.
    const x = X_SEC[pos], days = DAYS[pos];
    const idx = validIdx(y);
    const out = new Array(y.length).fill(null);
    if (idx.length < 2) return out;

    const from = startIndex(days, idx, lb).i;
    var n = 0, sx = 0, sy = 0, sxx = 0, sxy = 0;
    const x0 = x[from];
    for (var k = 0; k < idx.length; k++) {
      const j = idx[k];
      if (j < from) continue;
      const xv = (x[j] - x0) / 86400.0, yv = y[j];
      n++; sx += xv; sy += yv; sxx += xv * xv; sxy += xv * yv;
    }
    if (n < 2) return out;
    const den = n * sxx - sx * sx;
    if (den === 0) return out;
    const a = (n * sxy - sx * sy) / den;
    const b = (sy - a * sx) / n;
    for (var j2 = from; j2 < y.length; j2++) {
      out[j2] = a * ((x[j2] - x0) / 86400.0) + b;
    }
    return out;
  }

  function scaleBand(factor) {
    // Bandet ar relativt: +/-MEAN_BAND av dagens snitt, inte ett fast antal pips.
    return function (arr) {
      return arr.map(function (v) { return v === null ? null : v * factor; });
    };
  }

  function metrics(pair, bank, lb) {
    const pos = TRACE_POS[pair][bank];
    const days = DAYS[pos], x = X_SEC[pos];
    const yUnit = Y_DATA[state.unit][state.view][state.outliers][pos];
    const yPips = Y_DATA["pips"][state.view][state.outliers][pos];
    const yPct  = Y_DATA["pct"][state.view][state.outliers][pos];

    const idx = validIdx(yUnit);
    if (!idx.length) return null;

    const last = idx[idx.length - 1];
    const st = startIndex(days, idx, lb);
    const i = st.i;

    // Absolut: forandring i bankens paslag. Relativt: kundens marginal.
    const nowPips = yPips[last], pastPips = yPips[i];
    const nowMarg = yPct[last], pastMarg = yPct[i];

    const changePips = (nowPips !== null && pastPips !== null)
      ? nowPips - pastPips : null;
    const changeMargin = (nowMarg !== null && pastMarg !== null && pastMarg)
      ? (nowMarg / pastMarg - 1.0) * 100.0 : null;

    var nWin = 0;
    for (var k = 0; k < idx.length; k++) { if (idx[k] >= i) nWin++; }

    return {
      spreadNow: yUnit[last], spreadPast: yUnit[i],
      nowPips: nowPips, nowMargin: nowMarg,
      changePips: changePips, changeMargin: changeMargin,
      slope: slopePerDay(x, yPips, idx, i),
      nowDate: days[last], pastDate: days[i],
      targetDate: prevSessionOrSame(shiftDay(days[last], -lb)),
      approx: st.approx, nWindow: nWin, n: idx.length
    };
  }

  function buildRows(pair, lb) {
    // Jamforelsen mot jamforelsebanken gors pa SPREADEN, inte pa avistakursen:
    // bankerna scrapas vid olika tidpunkter och mid hinner rora sig daremellan.
    const baseBank = TABLE_BASELINE[pair];
    const have = Object.keys(TRACE_POS[pair]);
    const base = (have.indexOf(baseBank) >= 0) ? metrics(pair, baseBank, lb) : null;
    const swPips = base ? base.nowPips : null;
    const swMargin = base ? base.nowMargin : null;

    const got = [];
    for (var k = 0; k < have.length; k++) {
      const m = metrics(pair, have[k], lb);
      if (m) got.push({ bank: have[k], m: m });
    }
    if (!got.length) return [];

    var latest = got[0].m.nowDate;
    got.forEach(function (o) { if (o.m.nowDate > latest) latest = o.m.nowDate; });

    const rows = got.map(function (o) {
      const m = o.m, isBase = (o.bank === baseBank);
      var vsPips = null, vsMargin = null;
      if (!isBase) {
        if (swPips && m.nowPips !== null) vsPips = m.nowPips - swPips;
        if (swMargin && m.nowMargin !== null) vsMargin = (m.nowMargin / swMargin - 1.0) * 100.0;
      }
      const stale = m.nowDate < latest;
      return {
        bank: o.bank + (m.approx ? " *" : "") + (stale ? " \\u2020" : ""),
        isBase: isBase, stale: stale, approx: m.approx,
        spreadNow: m.spreadNow, spreadPast: m.spreadPast,
        change: m.changePips, changeMargin: m.changeMargin, slope: m.slope,
        vsPips: vsPips, vsMargin: vsMargin,
        nowDate: m.nowDate, pastDate: m.pastDate, targetDate: m.targetDate,
        nWindow: m.nWindow, n: m.n
      };
    });

    rows.sort(function (a, b) {
      if (a.spreadNow === null) return 1;
      if (b.spreadNow === null) return -1;
      return a.spreadNow - b.spreadNow;
    });
    return rows;
  }

  // -------------------------------------------------------- rendering ----

  function hasMean(pair) { return MEAN_PAIRS.indexOf(pair) >= 0; }

  function applyVisibility() {
    // En enda visible-array over alla traces: parvalet styr grunden, medan
    // "Observationer" och "Snitt" slar av sina egna sorter ovanpa det.
    const gd = document.getElementById("fxplot");
    const vis = TRACE_KIND.map(function (kind, i) {
      if (TRACE_PAIRS[i] !== state.pair) return false;
      if (kind === "data") return state.points !== "off";
      if (kind === "mean" || kind === "band") return state.mean;
      return true;
    });
    Plotly.restyle(gd, { visible: vis });
  }

  function applyData() {
    const gd = document.getElementById("fxplot");
    const ys = Y_DATA[state.unit][state.view][state.outliers];
    Plotly.restyle(gd, { y: ys, hovertemplate: HOVER_T[state.unit] }, DATA_IDX);
    Plotly.restyle(gd,
      { mode: state.points === "line" ? "lines" : "lines+markers" }, DATA_IDX);

    const t = state.trend
      ? ys.map(function (y, pos) { return trendLine(pos, y, state.lookback); })
      : TREND_OFF;
    Plotly.restyle(gd, { y: t }, TREND_IDX);

    // Snittet beror inte av fonstret, sa det racker att rakna om nar det
    // faktiskt visas - sliderdrag ror det inte.
    if (state.mean && MEAN_IDX.length) {
      const ym = MEAN_Y[state.unit][state.view][state.outliers];
      const nm = MEAN_N[state.unit][state.view][state.outliers];
      Plotly.restyle(gd,
        { y: ym, customdata: nm, hovertemplate: MEAN_HOVER[state.unit] }, MEAN_IDX);
      Plotly.restyle(gd, { y: ym.map(scaleBand(1 - MEAN_BAND)) }, BAND_LO_IDX);
      Plotly.restyle(gd, { y: ym.map(scaleBand(1 + MEAN_BAND)) }, BAND_HI_IDX);
    }

    Plotly.relayout(gd, {
      "xaxis.rangebreaks": state.view === "bank" ? BANKDAY_BREAKS : [],
      "yaxis.title.text": Y_TITLE[state.unit],
      "yaxis.autorange": true
    });
    renderTable();
  }

  function render() {
    applyVisibility();
    applyData();
  }

  function fmt(v, sign, digits) {
    if (v === null || v === undefined) return DASH;
    const d = (digits === undefined) ? 3 : digits;
    const s = (sign && v > 0) ? "+" : "";
    return s + v.toFixed(d);
  }

  function renderTable() {
    const pair = state.pair;
    const cfg = UNIT_CFG[state.unit];
    const lb = state.lookback;
    const rows = buildRows(pair, lb);
    const viewTxt = state.view === "bank" ? "endast bankdagar (XSTO)" : "alla dagar";
    const outTxt = state.outliers === "clean" ? "outliers rensade" : "outliers inkluderade";

    document.getElementById("thNow").textContent = "Spread nu (" + cfg.head + ")";
    document.getElementById("thPast").textContent =
      "Spread ~" + lb + " d sedan (" + cfg.head + ")";
    document.getElementById("thChange").textContent = "\\u0394 " + lb + " d (pips)";
    document.getElementById("thMargin").textContent = "\\u0394 marginal " + lb + " d (%)";

    document.getElementById("tableCaption").textContent =
      pair + " " + DASH + " " + cfg.name + ", " + viewTxt + ", " + outTxt +
      ", " + lb + " dagars f\\u00f6nster " + DASH + " j\\u00e4mf\\u00f6relsebank: " +
      (TABLE_BASELINE[pair] || DASH) + " " + DASH + " sorterad p\\u00e5 smalast spread";

    const cls = function (v) { return v === null ? "" : (v > 0 ? "up" : (v < 0 ? "down" : "")); };
    var anyApprox = false, anyStale = false, anyThin = false;

    document.querySelector("#statsTable tbody").innerHTML = rows.map(function (r) {
      if (r.approx) { anyApprox = true; }
      if (r.stale)  { anyStale = true; }
      if (r.nWindow < 3) { anyThin = true; }
      const title = "Nu: " + r.nowDate + " \\u2022 j\\u00e4mf\\u00f6relsepunkt: " + r.pastDate +
                    " (m\\u00e5ldag efter bankdagsjustering: " + r.targetDate +
                    ") \\u2022 " + r.nWindow + " observationer i f\\u00f6nstret, " +
                    r.n + " totalt";
      return "<tr class='" + (r.isBase ? "base" : "") + "' title=\\"" + title + "\\">" +
        "<td>" + r.bank + "</td>" +
        "<td>" + fmt(r.spreadNow, false, cfg.val) + "</td>" +
        "<td>" + fmt(r.spreadPast, false, cfg.val) + "</td>" +
        "<td class='" + cls(r.change) + "'>" + fmt(r.change, true, 1) + "</td>" +
        "<td class='" + cls(r.changeMargin) + "'>" + fmt(r.changeMargin, true, 2) + "</td>" +
        "<td class='" + cls(r.slope) + "'>" + fmt(r.slope, true, 2) + "</td>" +
        "<td class='" + cls(r.vsPips) + "'>" + fmt(r.vsPips, true, 1) + "</td>" +
        "<td class='" + cls(r.vsMargin) + "'>" + fmt(r.vsMargin, true, 1) + "</td>" +
        "</tr>";
    }).join("");

    var meanNote = "";
    if (state.mean) {
      const bl = hasMean(pair) ? MEAN_BANKS[pair] : [];
      meanNote = bl.length
        ? " Den streckade linjen \\u00e4r dagens medelspread f\\u00f6r " +
          bl.join(", ") + "; det skuggade bandet \\u00e4r \\u00b1" +
          Math.round(MEAN_BAND * 100) + " % av det snittet. Swedbank ing\\u00e5r " +
          "inte, eftersom den \\u00e4r j\\u00e4mf\\u00f6relsebank i tabellen. " +
          "Saknar en bank en dag anv\\u00e4nds dess senaste niv\\u00e5 (h\\u00f6gst " +
          MEAN_FFILL_DAYS + " dagar) s\\u00e5 att snittet r\\u00e4knas p\\u00e5 samma " +
          "banker hela tiden \\u2013 h\\u00e5ll muspekaren \\u00f6ver linjen f\\u00f6r " +
          "att se hur m\\u00e5nga som hade en egen notering. Dagar med f\\u00e4rre \\u00e4n " +
          MEAN_MIN_BANKS + " banker utel\\u00e4mnas helt och \\u00f6verbryggas."
        : " Inget banksnitt f\\u00f6r det h\\u00e4r paret \\u2013 f\\u00f6r f\\u00e5 banker " +
          "utanf\\u00f6r Swedbank har j\\u00e4mf\\u00f6rbar spread.";
    }

    document.getElementById("tableFoot").innerHTML =
      "1 pip = " + PIP_SIZES[pair] + " " + pair.split("/")[1] + ". " +
      "\\u0394 pips = bankens eget p\\u00e5slag i prissteg. \\u0394 marginal = " +
      "f\\u00f6r\\u00e4ndringen i spread/mid, allts\\u00e5 kundens relativa kostnad. " +
      "Skiljer de sig \\u00e5t kommer mellanskillnaden fr\\u00e5n marknaden: 0 pips men " +
      "positiv \\u0394 marginal betyder att banken st\\u00e5tt stilla medan valutan fallit. " +
      "Samma uppdelning g\\u00e4ller j\\u00e4mf\\u00f6relsen mot j\\u00e4mf\\u00f6relsebanken: " +
      "pipsdifferensen \\u00e4r skillnaden i p\\u00e5slag, marginaldifferensen hur mycket " +
      "dyrare eller billigare kunden handlar relativt sett. " +
      "F\\u00f6nstret styr b\\u00e5de delta-, trend- och j\\u00e4mf\\u00f6relsekolumnerna " +
      "samt trendlinjen i grafen. Positiva v\\u00e4rden = bredare spread \\u00e4n " +
      "f\\u00f6rr / \\u00e4n j\\u00e4mf\\u00f6relsebanken." +
      (anyApprox ? " <b>*</b> = kortare historik \\u00e4n " + lb +
                   " dagar; \\u00e4ldsta punkten anv\\u00e4nds." : "") +
      (anyStale ? " <b>\\u2020</b> = senaste observationen \\u00e4r \\u00e4ldre \\u00e4n " +
                  "\\u00f6vriga bankers." : "") +
      (anyThin ? " Obs: f\\u00e4rre \\u00e4n tre observationer i f\\u00f6nstret f\\u00f6r " +
                 "minst en bank \\u2013 trenden \\u00e4r d\\u00e5 mycket os\\u00e4ker." : "") +
      meanNote;
  }

  function setActive(groupId, btn) {
    document.getElementById(groupId).querySelectorAll("button")
      .forEach(function (x) { x.classList.remove("active"); });
    btn.classList.add("active");
  }

  function wire(groupId, key, coerce) {
    document.querySelectorAll("#" + groupId + " button").forEach(function (b) {
      b.addEventListener("click", function () {
        state[key] = coerce(b.dataset[key]);
        setActive(groupId, b);
        render();
      });
    });
  }

  function syncMeanControl() {
    // Saknar paret ett banksnitt finns det inget att sla pa - graa ut knappen
    // i stallet for att lata den se trasig ut.
    const ok = hasMean(state.pair);
    document.querySelectorAll("#meanGroup button").forEach(function (b) {
      b.disabled = !ok;
    });
  }

  window.addEventListener("load", function () {
    // Byte av par styr synlighet separat fran ovriga kontroller.
    document.querySelectorAll("#pairGroup button").forEach(function (b) {
      b.addEventListener("click", function () {
        state.pair = b.dataset.pair;
        setActive("pairGroup", b);
        syncMeanControl();
        render();
      });
    });
    wire("unitGroup",    "unit",     function (v) { return v; });
    wire("viewGroup",    "view",     function (v) { return v; });
    wire("trendGroup",   "trend",    function (v) { return v === "on"; });
    wire("meanGroup",    "mean",     function (v) { return v === "on"; });
    wire("pointsGroup",  "points",   function (v) { return v; });
    wire("outlierGroup", "outliers", function (v) { return v; });

    // Slidern: etiketten uppdateras direkt, men omrakningen kors en gang per
    // ram sa att ett snabbt drag inte koar upp hundratals Plotly-anrop.
    // Synligheten paverkas inte av fonstret, sa har racker applyData().
    const slider = document.getElementById("lookbackSlider");
    const output = document.getElementById("lookbackValue");
    var pending = false;

    function showValue() { output.textContent = slider.value + " d"; }

    slider.addEventListener("input", function () {
      state.lookback = +slider.value;
      showValue();
      if (pending) return;
      pending = true;
      window.requestAnimationFrame(function () { pending = false; applyData(); });
    });

    showValue();
    syncMeanControl();
    render();
  });
</script>
</body>
</html>
"""

page = (
    PAGE.replace("__PLOT_DIV__", plot_div)
        .replace("__PAIR_BUTTONS__", pair_buttons_html)
        .replace("__Y_DATA__", json.dumps(dataY))
        .replace("__TREND_OFF__", json.dumps(trendOff))
        .replace("__X_SEC__", json.dumps(xSec))
        .replace("__DAYS__", json.dumps(dayStr))
        .replace("__BANK_OF__", json.dumps(bankOf, ensure_ascii=False))
        .replace("__TRACE_POS__", json.dumps(trace_pos, ensure_ascii=False))
        .replace("__HOVER_T__", json.dumps(hoverT))
        .replace("__Y_TITLE__", json.dumps({u: HOVER_UNIT[u][1] for u in UNITS}))
        .replace("__DATA_IDX__", json.dumps(data_idx))
        .replace("__TREND_IDX__", json.dumps(trend_idx))
        .replace("__BANKDAY_BREAKS__", json.dumps(bankday_breaks))
        .replace("__TRACE_PAIRS__", json.dumps(trace_pairs))
        .replace("__TRACE_KIND__", json.dumps(trace_kind))
        .replace("__PRESENT_PAIRS__", json.dumps(present_pairs))
        .replace("__TABLE_BASELINE__", json.dumps(table_baseline, ensure_ascii=False))
        .replace("__PIP_SIZES__", json.dumps(pip_sizes))
        .replace("__SESSION_DAYS__", json.dumps(session_days))
        .replace("__DAY_RANGE__", json.dumps(day_range))
        .replace("__MEAN_Y__", json.dumps(meanY))
        .replace("__MEAN_N__", json.dumps(meanN))
        .replace("__MEAN_HOVER__", json.dumps(MEAN_HOVER))
        .replace("__MEAN_IDX__", json.dumps(mean_idx))
        .replace("__BAND_LO_IDX__", json.dumps(band_lo_idx))
        .replace("__BAND_HI_IDX__", json.dumps(band_hi_idx))
        .replace("__MEAN_PAIRS__", json.dumps(mean_pairs))
        .replace("__MEAN_BANKS__", json.dumps(mean_banks, ensure_ascii=False))
        .replace("__MEAN_BAND__", json.dumps(MEAN_BAND))
        .replace("__MEAN_MIN_BANKS__", json.dumps(MEAN_MIN_BANKS))
        .replace("__MEAN_FFILL_DAYS__", json.dumps(MEAN_FFILL_DAYS))
        .replace("__BAND_PCT__", str(int(MEAN_BAND * 100)))
        .replace("__MIN_LOOKBACK__", str(MIN_LOOKBACK))
        .replace("__MAX_LOOKBACK__", str(max_lookback))
        .replace("__DEFAULT_LOOKBACK__", json.dumps(default_lookback))
)

DOCS_DIR.mkdir(exist_ok=True)
out = DOCS_DIR / "index.html"
out.write_text(page, encoding="utf-8")
print(f"Skrev {out}")