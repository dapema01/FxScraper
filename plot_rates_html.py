import json
import re
from pathlib import Path

import exchange_calendars as xcals
import numpy as np
import pandas as pd
import plotly.graph_objects as go

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DOCS_DIR = Path(__file__).resolve().parent / "docs"
ALL_BANKS_PREFIX = "all_banks_"

# Vilka par som ska finnas i menyn. PAIR_SOURCE_BANKS avgor VEMS valutalista
# som satter urvalet - Swedbank ar jamforelsebank i tabellen, sa det ar
# deras utbud vi vill kunna folja. Sätt till None (eller tom tuple) for att
# ta med varje par som forekommer i filen, oavsett bank.
PAIR_SOURCE_BANKS = ("Swedbank", "Swedbank (Privat)")

# Par som ska ligga overst i menyn (i den har ordningen). Ovriga sorteras
# alfabetiskt under dem. Ett par som inte finns i datan hoppas over.
PAIR_ORDER_FIRST = ["USD/SEK", "EUR/SEK", "GBP/SEK", "NOK/SEK", "DKK/SEK"]

# Par med farre observationer an sa har lamnas utanfor menyn
MIN_PAIR_OBS = 3

PALETTE = ["#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A",
           "#19D3F3", "#FF6692", "#B6E880", "#FF97FF", "#FECB52"]
OUTLIER_THRESHOLD = 0.10   # >10 % avvikelse fran lokal median = spike
SWEDBANK_BASELINE = "Swedbank"
SWEDBANK_FALLBACK = "Swedbank (Privat)"

# Tillbakablicksfonstret ar en slider och kan darfor inte forberaknas.
# Delta-, trend- och jamforelsekolumnerna samt grafens trendlinje raknas
# i stallet om i webblasaren vid varje sliderdrag.
MIN_LOOKBACK = 3
DEFAULT_LOOKBACK = 30

# Grafens hojd satts i webblasaren till det som blir over av fonstret nar
# kontrollraden, tabellen och fotnoten tagit sitt, sa att hela sidan ryms utan
# scroll. Mats de bada varje gang tabellen ritas om, i stallet for att antas 
# vara nagon fast andel av sidan.
#
#   PLOT_MIN_HEIGHT   golv i pixlar. Ryms inte allt anda far sidan scrolla
#   PLOT_BOTTOM_GAP   luft under fotnoten sa den inte tangerar fonsterkanten

PLOT_MIN_HEIGHT = 320
PLOT_BOTTOM_GAP = 18

# En pip = minsta noterade prissteget i quote-valutan. Alla par ar <FX>/SEK
# och noteras med fyra decimaler, sa 1 pip = 0,0001 SEK.
#
# Undantagen ar JPY & HUF

DEFAULT_PIP_SIZE = 0.0001
PIP_SIZE_OVERRIDES = {
    "HUF/SEK": 0.00001,
    "JPY/SEK": 0.000001,
}

UNITS = ["pct", "pips"]          # % av mid / pips (absolut spread)
VIEWS = ["bank", "all"]          # bankdagar / alla dagar
OUTLIER_MODES = ["raw", "clean"] # visa spikar / rensa spikar

# Snittlinjen: dagligt medelvarde over "jamforelsegruppen", dvs alla banker
# utom Swedbank (foretag + privat). Swedbank ar tabellens jamforelsebank och
# ska darfor inte inga i det snitt. Handelsbanken faller bort automatiskt eftersom deras
# interbankflode inte ger nagon jamforbar spread.
#
# Alla banker noterar inte alla valutor: gruppen satts darfor per par, ur de
# banker som faktiskt har en jamforbar spread just i det paret. Par dar
# farre an MEAN_MIN_BANKS banker aterstar far inget snitt alls, och da graas
# Snitt-knappen ut i granssnittet.
MEAN_EXCLUDE = {"Swedbank", "Swedbank (Privat)"}
MEAN_MIN_BANKS = 2

# Bandbredden stalls i granssnittet (rutan bredvid Snitt-knappen), sa de har
# varderna ar bara startlage och tillatna granser for inmatningen.
MEAN_BAND_PCT = 10.0     # startvarde i rutan
MEAN_BAND_MIN = 0.0
MEAN_BAND_MAX = 100.0
MEAN_BAND_STEP = 0.5
MEAN_BAND = MEAN_BAND_PCT / 100.0   # startlage for de traces Python skickar ut

# Saknas en bank en enskild dag skulle snittet den dagen rakna pa en annan
# uppsattning banker an dagarna omkring - och eftersom bankerna ligger pa
# olika nivaer blir det ett hack i linjen som ser ut som en marknadsrorelse
# men bara ar en utebliven datapunkt. Darfor skrivs varje banks EGEN
# senast kanda dagsniva fram over hal.
#
# Limiten raknas i dagar dar NAGON bank rapporterade (helger och tomma dagar
# ater alltsa inte upp budgeten). Satt 0 for att sla av framskrivningen helt
# och None for obegransad.
MEAN_FFILL_DAYS = 3
MEAN_LINE_COLOR = "#2f3640"
MEAN_FILL_COLOR = "rgba(47, 54, 64, 0.13)"


def pip_size(pair: str) -> float:
    return PIP_SIZE_OVERRIDES.get(pair, DEFAULT_PIP_SIZE)


def pip_label(pip: float) -> str:
    """Pipstorleken som text, utan exponentnotation (0.00001, inte 1e-05)."""
    return f"{pip:.12f}".rstrip("0")


def band_label(pct: float) -> str:
    """Legendetikett for snittlinjen. Samma format som JS anvander vid
    omrakning, sa att etiketten ser likadan ut fore och efter forsta
    andringen i inmatningsrutan."""
    return f"Snitt banker +/-{pct:g} %"


def pair_sort_key(pair: str):
    """PAIR_ORDER_FIRST forst i angiven ordning, ovriga alfabetiskt efter."""
    if pair in PAIR_ORDER_FIRST:
        return (0, PAIR_ORDER_FIRST.index(pair), pair)
    return (1, 0, pair)


def latest_all_banks_file(output_dir: Path) -> Path:
    candidates = sorted(output_dir.glob(f"{ALL_BANKS_PREFIX}*.csv"))
    if not candidates:
        raise FileNotFoundError(f"Ingen {ALL_BANKS_PREFIX}*.csv i {output_dir}")
    return candidates[-1]


# Antal decimaler i den JSON som skickas till webblasaren. Spreadar noteras
# i tusendels procent respektive tiondels pip, sa sex decimaler ar rikligt.
JSON_DECIMALS = 6


def clean(vals):
    """NaN/inf -> None sa JSON blir giltig och Plotly ser dem som gap.

    Vektoriserad: serien gors om till en float-array i ett svep i stallet
    for att loopas element for element i Python.
    """
    a = np.asarray(vals, dtype="float64")
    a = np.round(a, JSON_DECIMALS)
    finite = np.isfinite(a)
    return [float(v) if ok else None for v, ok in zip(a.tolist(), finite.tolist())]


def mask_str(mask) -> str:
    """Bool-serie -> "010110...". En bitmask per serie i stallet for fyra
    fardigmaskade talarrayer - se kommentaren vid Y_DATA i JS-delen."""
    a = np.asarray(mask, dtype=bool)
    return "".join(np.where(a, "1", "0").tolist())


def dump(obj) -> str:
    """json.dumps utan onodig whitespace. Sparar 20-30 % pa stora arrayer."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------- data ----

path = latest_all_banks_file(OUTPUT_DIR)
df = pd.read_csv(path)

# Blandade UTC-offsets (sommar-/vintertid) i scraped_at: las som UTC och
# konvertera till svensk tid, sa att .dt-accessorn alltid fungerar.
df["scraped_at"] = pd.to_datetime(df["scraped_at"], utc=True).dt.tz_convert("Europe/Stockholm")
df = df[df["pair"].notna()].copy()


def select_pairs(frame: pd.DataFrame) -> list:
    """Valutalistan: alla par som PAIR_SOURCE_BANKS noterar.

    Faller tillbaka pa samtliga par i filen om de bankerna saknas helt (t.ex.
    en korning dar Swedbank-scrapern gatt fel), sa att sidan aldrig blir tom.
    """
    if PAIR_SOURCE_BANKS:
        src = frame[frame["bank"].isin(PAIR_SOURCE_BANKS)]
        pairs = set(src["pair"].unique())
        if not pairs:
            print("Varning: ingen av kallbankerna "
                  f"({', '.join(PAIR_SOURCE_BANKS)}) finns i {path.name} - "
                  "visar samtliga par i filen i stallet.")
            pairs = set(frame["pair"].unique())
    else:
        pairs = set(frame["pair"].unique())

    counts = frame["pair"].value_counts()
    return sorted(
        (p for p in pairs if counts.get(p, 0) >= MIN_PAIR_OBS),
        key=pair_sort_key,
    )


wanted_pairs = select_pairs(df)
if not wanted_pairs:
    raise SystemExit(f"Inga par med minst {MIN_PAIR_OBS} observationer i {path}")

df = df[df["pair"].isin(wanted_pairs)].copy()
df = df.sort_values("scraped_at")

# Tva matt pa samma spread:
#   spread_pct  = andel av mid, jamforbart mellan par och over tid.
#   spread_pips = absolut spread i prissteg (SEK per enhet / pipstorlek),
#                 dvs det matt en FX-desk faktiskt pratar i.
df["spread_pct"] = df["spread"] / df["mid"] * 100
df["spread_pips"] = df["spread"] / df["pair"].map(pip_size)

# Naiv, normaliserad kalenderdag - anvands for bankdagslogik och uppslag bakat.
df["day"] = df["scraped_at"].dt.tz_localize(None).dt.normalize()

# Datumintervallet vi faktiskt har data for. Behovs bade for axelbrotten
# langre ner och for att hamta hela bankdagskalendern i ett svep har.
first_day = df["day"].min()
last_day = df["day"].max()
all_days = pd.date_range(first_day, last_day, freq="D")

xsto = xcals.get_calendar("XSTO")


def _sessions_in(first, last):
    """Hela XSTO-kalendern for intervallet som en mangd, hamtad en gang.

    Ett mangduppslag ar storleksordningar billigare an ett
    xsto.is_session()-anrop per dag, och `keep_mask` blir en vektoriserad
    isin() i stallet for en Python-loop over varje enskild observation.
    """
    try:
        idx = pd.DatetimeIndex(xsto.sessions_in_range(first, last))
    except Exception:
        return None
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return set(idx.normalize())


SESSIONS = _sessions_in(first_day, last_day)


def is_session(day) -> bool:
    """Bankdagskoll (XSTO). Utanfor kalenderns rackvidd: falla tillbaka pa vardag."""
    key = pd.Timestamp(day).normalize()
    if SESSIONS is not None and first_day <= key <= last_day:
        return key in SESSIONS
    return key.weekday() < 5


def keep_mask(g):
    if SESSIONS is not None:
        return g["day"].isin(SESSIONS)
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
# backa maldagen till narmaste session. Bada faller ur samma bool-mask.
if SESSIONS is not None:
    is_sess = all_days.isin(SESSIONS)
else:
    is_sess = all_days.weekday < 5
is_weekday = all_days.weekday < 5

holiday_breaks = all_days[is_weekday & ~is_sess].strftime("%Y-%m-%d").tolist()
bankday_breaks = [dict(bounds=["sat", "mon"])]
if holiday_breaks:
    bankday_breaks.append(dict(values=holiday_breaks))

session_days = all_days[is_sess].strftime("%Y-%m-%d").tolist()
day_range = [first_day.strftime("%Y-%m-%d"), last_day.strftime("%Y-%m-%d")]

# Sliderns tak: hela den historik vi faktiskt har.
max_lookback = max(MIN_LOOKBACK + 1, int((last_day - first_day).days))
default_lookback = min(DEFAULT_LOOKBACK, max_lookback)

banks = sorted(df["bank"].unique())
bank_color = {b: PALETTE[i % len(PALETTE)] for i, b in enumerate(banks)}


# ------------------------------------------------- serier per bank/par ----

# En enda gang per (par, bank): sortera, maska bankdagar, hitta spikar.
# Bade grafen och tabellen laser ur den har cachen, sa att siffrorna i
# tabellen alltid ar exakt de punkter som ritas i den valda vyn.
series_cache = {}

# En enda groupby over bade par och bank
for (pair, bank), g in df.groupby(["pair", "bank"], sort=True):
    g = g.sort_values("scraped_at").reset_index(drop=True)
    if g["spread_pct"].notna().sum() == 0:
        continue  # t.ex. Handelsbanken: bara mid, ingen jamforbar spread

    km = keep_mask(g)
    spikes = detect_outliers(g["spread_pct"], km)

    c = {"g": g, "km": km, "spikes": spikes}
    for unit, s in (("pct", g["spread_pct"]), ("pips", g["spread_pips"])):
        cleaned = s.where(~spikes)
        c[(unit, "all", "raw")] = s
        c[(unit, "all", "clean")] = cleaned
        c[(unit, "bank", "raw")] = s.where(km)
        c[(unit, "bank", "clean")] = cleaned.where(km)

    series_cache[(pair, bank)] = c

table_banks = {}
for (pair, bank) in series_cache:
    table_banks.setdefault(pair, []).append(bank)
for pair in table_banks:
    table_banks[pair].sort()

# Ett par utan en enda jamforbar serie lyfts ur menyn.
present_pairs = [p for p in wanted_pairs if table_banks.get(p)]
if not present_pairs:
    raise SystemExit(f"Inget par i {path} har en jamforbar spread att rita")

dropped = [p for p in wanted_pairs if p not in present_pairs]
if dropped:
    print(f"Hoppar over {len(dropped)} par utan jamforbar spread: "
          f"{', '.join(dropped)}")


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
 
    Gruppen satts per par: bara de banker som faktiskt noterar paret och har
    en jamforbar spread ingar. Ett par som bara Swedbank (och eventuellt en
    till) noterar far darfor inget snitt alls.
 
    Tva steg: forst medel per (dag, bank), sedan medel over banker.
 
    Det forsta steget ar i praktiken en no-op sa lange kallan ar
    all_banks-filen: main.py slanger dagens tidigare rader innan den skriver
    (_drop_today_rows), sa varje bank har hogst en notering per dag och
    medelvardet av ett enda varde ar vardet sjalvt. Steget star kvar anda,
    dels for att det garanterar att (dag, bank) ar unik - vilket
    unstack("bank") langre ner kraver - dels for att det blir barande ifall
    kallan innehaller flera scrapes per dag. 
 
    Snittet raknas per (enhet, vy, outlier-lage) pa exakt samma maskade
    serier som ritas i grafen, sa att linjen alltid beskriver de punkter
    du faktiskt ser: i bankdagsvyn ingar inga helgvarden, och med "Rensa"
    ar spikarna borta ur snittet ocksa.
    """
    banks_in = [b for b in table_banks[pair] if b not in MEAN_EXCLUDE]
    if len(banks_in) < MEAN_MIN_BANKS:
        return None

    parts = []
    for bank in banks_in:
        c = series_cache[(pair, bank)]
        g = c["g"]
        d = pd.DataFrame({
            "day": g["day"].to_numpy(),
            "bank": bank,
            "ts_ns": g["scraped_at"].values.astype("datetime64[ns]").astype("int64"),
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
# Par -> plats i mean_pairs, sa att JS kan rora exakt det synliga parets
# tre snitt-traces i stallet for allas.
mean_pos = {p: i for i, p in enumerate(mean_pairs)}

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

# Med hela Swedbanks valutalista blir det manga fler serier an de fem par
# sidan borjade med. Darfor skickas tva (procent och pips) plus tva bitmasker
# (bankdag / spike). JS satter ihop de atta kombinationerna vid inladdning -
# samma innehall som forr, men ungefar en fjardedel sa mycket JSON.
baseY = {u: [] for u in UNITS}
keepMask, spikeMask = [], []

# Hover-mallen ar identisk mellan alla serier sa nar som pa banknamnet. Vi
# skickar darfor ut EN mall per enhet och later JS satta in namnet, i stallet
# for att duplicera hela strangen en gang per bank OCH par.
HOVER_TPL = {
    u: ("<b>%BANK%</b><br>"
        "%{x|%Y-%m-%d %H:%M}<br>"
        + HOVER_UNIT[u][0] + "<br>"
        "bid: %{customdata[0]:.4f}<br>"
        "ask: %{customdata[1]:.4f}<br>"
        "mid: %{customdata[2]:.4f}<extra></extra>")
    for u in UNITS
}

# JS behover x-axeln i sekunder och kalenderdagen per punkt for att kunna
# hitta jamforelsepunkten och anpassa lutningen sjalv. seriesLen racker for
# att bygga "trend av"-arrayerna pa klienten - de innehaller bara nullar och
# behover inte skickas over natet.
xSec, dayStr, bankOf, seriesLen = [], [], [], []
trace_pos = {}
pair_pos = {}

for pair in present_pairs:
    trace_pos[pair] = {}
    pair_pos[pair] = []
    for bank in table_banks[pair]:
        c = series_cache[(pair, bank)]
        g = c["g"]
        color = bank_color[bank]
        vis = (pair == present_pairs[0])

        pos = len(xSec)
        trace_pos[pair][bank] = pos
        pair_pos[pair].append(pos)
        # Vektoriserat i stallet for en Python-loop per punkt: int64-nanos
        # -> sekunder, och strftime pa hela kolumnen.
        # Via datetime64[s] i stallet for att dela med 10**9: pandas 2.x
        # behaller kallans upplosning (us eller ns), sa en fast divisor blir
        # tyst fel med faktor 1000.
        xSec.append(g["scraped_at"].values.astype("datetime64[s]").astype("int64").tolist())
        dayStr.append(g["day"].dt.strftime("%Y-%m-%d").tolist())
        bankOf.append(bank)
        seriesLen.append(int(len(g)))

        baseY["pct"].append(clean(g["spread_pct"].tolist()))
        baseY["pips"].append(clean(g["spread_pips"].tolist()))
        keepMask.append(mask_str(c["km"]))
        spikeMask.append(mask_str(c["spikes"]))

        # --- Datatrace (start: % av mid, bankdagar, ra) ---
        idx = len(fig.data)
        fig.add_trace(
            go.Scatter(
                x=g["scraped_at"], y=clean(c[("pct", "bank", "raw")].tolist()),
                mode="lines+markers", name=bank,
                visible=vis, connectgaps=True, legendgroup=bank,
                line=dict(color=color), marker=dict(color=color),
                customdata=g[["bid", "ask", "mid"]].to_numpy(),
                hovertemplate=HOVER_TPL["pct"].replace("%BANK%", bank),
            )
        )
        trace_pairs.append(pair)
        trace_kind.append("data")
        data_idx.append(idx)

        # --- Trendtrace (start: dold), samma legendgroup som datatracen ---
        idx = len(fig.data)
        fig.add_trace(
            go.Scatter(
                x=g["scraped_at"], y=[None] * len(g), mode="lines",
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
            name=band_label(MEAN_BAND_PCT),
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
pip_labels = {pair: pip_label(pip_sizes[pair]) for pair in present_pairs}
pair_bank_count = {pair: len(table_banks[pair]) for pair in present_pairs}


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

# Grafen far fylla sin behallare (#plotwrap), vars hojd JS raknar ut ur
# fonsterhojden - se fitPlot(). 
plot_div = fig.to_html(
    full_html=False, include_plotlyjs="cdn", div_id="fxplot",
    default_height="100%", default_width="100%",
    config={"responsive": True},
)


def option_html(pair):
    n = pair_bank_count[pair]
    suffix = "bank" if n == 1 else "banker"
    return f'<option value="{pair}">{pair} \u2013 {n} {suffix}</option>'


# Menyn: huvudparen forst i sin egen grupp, resten av Swedbanks lista under.
# Med ett trettiotal valutor ar grupperingen skillnaden mellan en lista att
# leta i och en att valja ur.
head_pairs = [p for p in present_pairs if p in PAIR_ORDER_FIRST]
tail_pairs = [p for p in present_pairs if p not in PAIR_ORDER_FIRST]

if head_pairs and tail_pairs:
    pair_options_html = (
        '<optgroup label="Huvudpar">'
        + "".join(option_html(p) for p in head_pairs)
        + '</optgroup><optgroup label="&Ouml;vriga valutor">'
        + "".join(option_html(p) for p in tail_pairs)
        + "</optgroup>"
    )
else:
    pair_options_html = "".join(option_html(p) for p in present_pairs)


PAGE = """<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<title>FX-spread</title>
<style>
  /* Reservera rullningslistens bredd permanent. Utan det andras sidbredden i
     samma stund som listen forsvinner, fotnoten far plats pa farre rader, och
     hojdmatningen far ett nytt svar an den nyss raknade fram. */
  html { scrollbar-gutter: stable; }
  body { font-family: system-ui, sans-serif; margin: 8px 12px; }
  .controls { display: flex; gap: 20px; margin-bottom: 6px; flex-wrap: wrap;
              align-items: center;
              /* Klistrig: grafen tar hela vikningen, sa utan det har hamnar
                 kontrollerna utom rackhall sa fort man scrollar ner till
                 tabellen. */
              position: sticky; top: 0; z-index: 5; background: #fff;
              padding: 4px 0 6px; }
  .group { display: inline-flex; border: 1px solid #ccc; border-radius: 8px; overflow: hidden; }
  .group button { border: 0; padding: 6px 14px; background: #f5f5f5; cursor: pointer; font-size: 14px; }
  .group button + button { border-left: 1px solid #ccc; }
  .group button.active { background: #636EFA; color: #fff; }
  .group button:disabled { color: #aaa; cursor: not-allowed; }
  .label { font-size: 12px; color: #666; align-self: center; margin-right: 4px; }

  select#pairSelect { font-family: inherit; font-size: 14px; padding: 6px 10px;
                      border: 1px solid #ccc; border-radius: 8px; background: #f5f5f5;
                      cursor: pointer; min-width: 200px;
                      font-variant-numeric: tabular-nums; }
  select#pairSelect:focus { outline: 2px solid #636EFA; outline-offset: -1px; }

  .bandbox { display: inline-flex; align-items: center; gap: 2px; }
  .bandbox input[type=number] { width: 68px; padding: 5px 6px; font-size: 14px;
                                border: 1px solid #ccc; border-radius: 8px;
                                font-variant-numeric: tabular-nums; text-align: right; }
  .bandbox input[type=number]:focus { outline: 2px solid #636EFA; outline-offset: -1px; }
  .bandbox input[type=number]:disabled { background: #f5f5f5; color: #aaa; cursor: not-allowed; }
  .bandbox input.invalid { border-color: #c0392b; }

  .slider { display: inline-flex; align-items: center; gap: 8px; }
  .slider input[type=range] { width: 170px; accent-color: #636EFA; cursor: pointer; }
  .slider output { font-size: 14px; font-variant-numeric: tabular-nums;
                   min-width: 52px; color: #333; }

  /* Hojden skrivs over av fitPlot() sa fort JS kor; 45vh ar bara ett rimligt
     utgangslage innan tabellens och fotnotens hojd hunnit matas. */
  #plotwrap { height: 45vh; min-height: __PLOT_MIN_HEIGHT__px; width: 100%; }

  /* Atta kolumner far inte plats pa en telefon. Lat tabellen rulla i sidled
     for sig sjalv i stallet for att dra ut hela sidan. */
  .tablewrap { overflow-x: auto; }

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
  .foot { font-size: 12px; color: #777; margin-bottom: 8px; line-height: 1.5; }
  .foot p { margin: 0 0 7px; }
  .foot p:last-child { margin-bottom: 0; }
</style>
</head>
<body>
<div class="controls">
  <div><span class="label">Par:</span>
    <select id="pairSelect" title="Alla valutor Swedbank noterar">__PAIR_OPTIONS__</select>
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
  <div><span class="label">Snitt:</span>
    <div class="group" id="meanGroup">
      <button data-mean="off" class="active">Av</button>
      <button data-mean="on">P&aring;</button>
    </div>
  </div>
  <div class="bandbox"><span class="label">Band &plusmn;</span>
    <input type="number" id="bandInput" min="__BAND_MIN__" max="__BAND_MAX__"
           step="__BAND_STEP__" value="__BAND_PCT__"
           title="Bandets bredd i procent AV dagens snitt">
    <span class="label">%</span>
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
<div id="plotwrap">__PLOT_DIV__</div>
<div class="tablewrap">
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
      <th id="thVsPips"></th>
      <th id="thVsMargin"></th>
    </tr>
  </thead>
  <tbody></tbody>
</table>
</div>
<div class="foot" id="tableFoot"></div>
<script>
  const UNITS = __UNITS__;
  const BASE_Y = __BASE_Y__;
  const KEEP_MASK = __KEEP_MASK__;
  const SPIKE_MASK = __SPIKE_MASK__;
  const SERIES_LEN = __SERIES_LEN__;
  const X_SEC = __X_SEC__;
  const DAYS = __DAYS__;
  const BANK_OF = __BANK_OF__;
  const TRACE_POS = __TRACE_POS__;
  const PAIR_POS = __PAIR_POS__;
  const HOVER_TPL = __HOVER_TPL__;
  const Y_TITLE = __Y_TITLE__;
  const DATA_IDX = __DATA_IDX__;
  const TREND_IDX = __TREND_IDX__;
  const BANKDAY_BREAKS = __BANKDAY_BREAKS__;
  const TRACE_PAIRS = __TRACE_PAIRS__;
  const TRACE_KIND = __TRACE_KIND__;
  const PRESENT_PAIRS = __PRESENT_PAIRS__;
  const TABLE_BASELINE = __TABLE_BASELINE__;
  const PIP_LABELS = __PIP_LABELS__;
  const DEFAULT_PIP_LABEL = __DEFAULT_PIP_LABEL__;
  const PAIR_BANKS = __PAIR_BANKS__;
  const SESSION_DAYS = __SESSION_DAYS__;
  const DAY_RANGE = __DAY_RANGE__;
  const DEFAULT_LOOKBACK = __DEFAULT_LOOKBACK__;
  const PLOT_MIN_HEIGHT = __PLOT_MIN_HEIGHT__;
  const PLOT_BOTTOM_GAP = __PLOT_BOTTOM_GAP__;

  const MEAN_Y = __MEAN_Y__;
  const MEAN_N = __MEAN_N__;
  const MEAN_HOVER = __MEAN_HOVER__;
  const MEAN_IDX = __MEAN_IDX__;
  const BAND_LO_IDX = __BAND_LO_IDX__;
  const BAND_HI_IDX = __BAND_HI_IDX__;
  const MEAN_POS = __MEAN_POS__;
  const MEAN_BANKS = __MEAN_BANKS__;
  const MEAN_BAND_PCT = __MEAN_BAND_PCT__;
  const MEAN_BAND_MIN = __MEAN_BAND_MIN__;
  const MEAN_BAND_MAX = __MEAN_BAND_MAX__;
  const MEAN_MIN_BANKS = __MEAN_MIN_BANKS__;
  const MEAN_FFILL_DAYS = __MEAN_FFILL_DAYS__;

  // Hover-mallarna och de tomma trendarrayerna byggs har i stallet for att
  // skickas fardiga fran Python - de ar rena upprepningar.
  const HOVER_T = {};
  for (var u in HOVER_TPL) {
    HOVER_T[u] = BANK_OF.map(function (b) {
      return HOVER_TPL[u].replace("%BANK%", b);
    });
  }
  const TREND_OFF = SERIES_LEN.map(function (n) { return new Array(n).fill(null); });

  // Y_DATA[enhet][vy][outliers][serie] byggs har ur tva talserier och tva
  // bitmasker per serie. Python skickade forr alla atta kombinationerna
  // fardiga; med hela valutalistan blev det for mycket JSON for ingenting.
  const Y_DATA = {};
  UNITS.forEach(function (u) {
    Y_DATA[u] = { bank: { raw: [], clean: [] }, all: { raw: [], clean: [] } };
    for (var pos = 0; pos < BASE_Y[u].length; pos++) {
      const base = BASE_Y[u][pos];
      const km = KEEP_MASK[pos], sp = SPIKE_MASK[pos];
      const allClean = new Array(base.length);
      const bankRaw = new Array(base.length);
      const bankClean = new Array(base.length);
      for (var i = 0; i < base.length; i++) {
        const v = base[i];
        const spike = sp.charCodeAt(i) === 49;   // "1"
        const bankday = km.charCodeAt(i) === 49;
        allClean[i] = spike ? null : v;
        bankRaw[i] = bankday ? v : null;
        bankClean[i] = (bankday && !spike) ? v : null;
      }
      Y_DATA[u].all.raw.push(base);
      Y_DATA[u].all.clean.push(allClean);
      Y_DATA[u].bank.raw.push(bankRaw);
      Y_DATA[u].bank.clean.push(bankClean);
    }
  });

  const SESSIONS = new Set(SESSION_DAYS);
  const DASH = "\\u2013";
  const UNIT_CFG = {
    // label = vad storheten heter i den valda enheten. Procent ar en andel av
    // mid och kallas darfor marginal; pips ar det absoluta paslaget, dvs spread.
    pct:  { val: 3, head: "%",    label: "Marginal", name: "% av mid" },
    pips: { val: 1, head: "pips", label: "Spread",   name: "pips" }
  };

  const state = { pair: PRESENT_PAIRS[0], unit: "pct", view: "bank",
                  lookback: DEFAULT_LOOKBACK, trend: false, outliers: "raw",
                  mean: false, band: MEAN_BAND_PCT, points: "both" };

  // Cachade DOM-referenser: renderTable korde tidigare tio
  // getElementById-uppslag vid varje sliderdrag.
  var gd, elCaption, elFoot, elTbody, elThNow, elThPast, elThChange, elThMargin,
      elThVsPips, elThVsMargin, elBand, elPlotWrap, elControls, elTableWrap;

  function cacheDom() {
    gd           = document.getElementById("fxplot");
    elPlotWrap   = document.getElementById("plotwrap");
    elControls   = document.querySelector(".controls");
    elTableWrap  = document.querySelector(".tablewrap");
    elCaption    = document.getElementById("tableCaption");
    elFoot       = document.getElementById("tableFoot");
    elTbody      = document.querySelector("#statsTable tbody");
    elThNow      = document.getElementById("thNow");
    elThPast     = document.getElementById("thPast");
    elThChange   = document.getElementById("thChange");
    elThMargin   = document.getElementById("thMargin");
    elThVsPips   = document.getElementById("thVsPips");
    elThVsMargin = document.getElementById("thVsMargin");
    elBand       = document.getElementById("bandInput");
  }

  // Grafen far det som blir over av fonstret nar kontrollraden ovanfor och
  // tabellen plus fotnoten nedanfor tagit sitt - hela sidan ska rymmas utan
  // scroll.
  //
  // Utrymmet under grafen mats som avstandet fran grafens underkant till
  // fotnotens, alltsa pa den faktiska layouten. Det tar med marginalerna
  // mellan elementen utan att de behover raknas upp har, och foljer med nar
  // tabellen byter antal rader eller fotnoten far en rad forbehall extra.
  //
  // Grafens egen position mats i DOKUMENTET (rect.top + scrollY), inte i
  // fonstret: annars skulle varje scroll ge ett nytt svar. Avstandet nedat ar
  // en skillnad mellan tva rektanglar och ar scrolloberoende av sig sjalvt.
  var lastPlotHeight = 0;

  function fitPlot() {
    if (!elPlotWrap || !elFoot) return;
    const wrapRect = elPlotWrap.getBoundingClientRect();
    const top = wrapRect.top + window.scrollY;
    const below = elFoot.getBoundingClientRect().bottom - wrapRect.bottom;
    const avail = window.innerHeight - top - below - PLOT_BOTTOM_GAP;
    const h = Math.round(Math.max(PLOT_MIN_HEIGHT, avail));

    // En pixels skillnad ar inte vard en omritning: resize ar dyrt och
    // adressfaltet pa mobilen fjadrar innerHeight nagra pixlar vid scroll.
    if (Math.abs(h - lastPlotHeight) < 2) return;
    lastPlotHeight = h;
    elPlotWrap.style.height = h + "px";
    if (gd && window.Plotly && Plotly.Plots) Plotly.Plots.resize(gd);
  }

  // Delas av alla anropare (fonstret, observern, renderTable): en omrakning
  // per ram, oavsett hur manga av dem som utloses samtidigt.
  const fitThrottled = rafThrottle(fitPlot);

  function bandFrac() { return state.band / 100.0; }

  function bandText() {
    // Samma format som Python anvander for legendetiketten: inga onodiga
    // decimaler, men behall dem nar anvandaren skrivit in t.ex. 7,5.
    return String(Math.round(state.band * 100) / 100);
  }

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

  function scaleBand(arr, factor) {
    // Andel av dagens snitt, inte ett fast antal pips - se applyBand.
    return arr.map(function (v) { return v === null ? null : v * factor; });
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

  function hasMean(pair) { return MEAN_POS[pair] !== undefined; }

  function applyVisibility() {
    // En enda visible-array over alla traces: parvalet styr grunden, medan
    // "Observationer" och "Snitt" slar av sina egna sorter ovanpa det.
    const vis = TRACE_KIND.map(function (kind, i) {
      if (TRACE_PAIRS[i] !== state.pair) return false;
      if (kind === "data") return state.points !== "off";
      if (kind === "mean" || kind === "band") return state.mean;
      return true;
    });
    Plotly.restyle(gd, { visible: vis });
  }

  // Uppdelningen speglar vad som faktiskt beror pa vad:
  //   applyStatic  - enhet / vy / outliers / punkter / axlar / snittlinje
  //   applyBand    - enbart bandbredden i procentrutan
  //   applyWindow  - enbart tillbakablicksfonstret (trendlinje + tabell)
  // Allt rors bara for det valda paret: med hela valutalistan ligger de
  // allra flesta traces dolda, och de behover inte ritas om i onodan.

  function applyStatic() {
    const ys = Y_DATA[state.unit][state.view][state.outliers];
    const hov = HOVER_T[state.unit];
    const pos = PAIR_POS[state.pair];
    const idxs = [], vals = [], tpls = [];
    for (var k = 0; k < pos.length; k++) {
      idxs.push(DATA_IDX[pos[k]]);
      vals.push(ys[pos[k]]);
      tpls.push(hov[pos[k]]);
    }
    // Ett enda restyle-anrop: varje anrop kostar en egen redraw.
    if (idxs.length) {
      Plotly.restyle(gd, {
        y: vals,
        hovertemplate: tpls,
        mode: state.points === "line" ? "lines" : "lines+markers"
      }, idxs);
    }

    if (state.mean) applyMean();

    Plotly.relayout(gd, {
      "xaxis.rangebreaks": state.view === "bank" ? BANKDAY_BREAKS : [],
      "yaxis.title.text": Y_TITLE[state.unit],
      "yaxis.autorange": true
    });
  }

  function applyMean() {
    // Sjalva snittlinjen beror varken av fonstret eller av bandbredden, sa
    // den racker att rakna om nar par/enhet/vy/outliers andras.
    const k = MEAN_POS[state.pair];
    if (k === undefined) return;
    const ym = MEAN_Y[state.unit][state.view][state.outliers][k];
    const nm = MEAN_N[state.unit][state.view][state.outliers][k];
    Plotly.restyle(gd,
      { y: [ym], customdata: [nm], hovertemplate: MEAN_HOVER[state.unit] },
      [MEAN_IDX[k]]);
    applyBand();
  }

  function applyBand() {
    // Bandet ar relativt: +/-band % AV dagens snitt. Bara de tva kantlinjerna
    // och legendetiketten rors nar procentsatsen andras.
    const k = MEAN_POS[state.pair];
    if (k === undefined) return;
    const ym = MEAN_Y[state.unit][state.view][state.outliers][k];
    const f = bandFrac();
    Plotly.restyle(gd, { y: [scaleBand(ym, 1 - f)] }, [BAND_LO_IDX[k]]);
    Plotly.restyle(gd, { y: [scaleBand(ym, 1 + f)] }, [BAND_HI_IDX[k]]);
    Plotly.restyle(gd, { name: "Snitt banker +/-" + bandText() + " %" }, [MEAN_IDX[k]]);
  }

  function applyTrend() {
    // Bara det synliga parets trendlinjer anpassas. Tidigare kordes en
    // minsta-kvadrat-anpassning for SAMTLIGA par vid varje sliderdrag,
    // varav de dolda kastades direkt.
    const ys = Y_DATA[state.unit][state.view][state.outliers];
    const pos = PAIR_POS[state.pair];
    const idxs = [], vals = [];
    for (var k = 0; k < pos.length; k++) {
      idxs.push(TREND_IDX[pos[k]]);
      vals.push(state.trend
        ? trendLine(pos[k], ys[pos[k]], state.lookback)
        : TREND_OFF[pos[k]]);
    }
    if (idxs.length) Plotly.restyle(gd, { y: vals }, idxs);
  }

  function applyWindow() {
    applyTrend();
    renderTable();
  }

  function render() {
    applyVisibility();
    applyStatic();
    applyWindow();
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
    const baseBank = TABLE_BASELINE[pair];
    const viewTxt = state.view === "bank" ? "endast bankdagar (XSTO)" : "alla dagar";
    const outTxt = state.outliers === "clean" ? "outliers rensade" : "outliers inkluderade";

    elThNow.textContent = cfg.label + " nu (" + cfg.head + ")";
    elThPast.textContent = cfg.label + " ~" + lb + " d sedan (" + cfg.head + ")";
    elThChange.textContent = "\\u0394 " + lb + " d (pips)";
    elThMargin.textContent = "\\u0394 marginal " + lb + " d (%)";
    // Jamforelsebanken kan variera mellan par: alla banker noterar inte alla
    // valutor, sa rubriken skrivs ut ur TABLE_BASELINE i stallet for att sta
    // hardkodad som "vs Swedbank".
    elThVsPips.textContent = "Spread vs " + baseBank + " (pips)";
    elThVsMargin.textContent = "Marginal vs " + baseBank + " (%)";

    const nBanks = PAIR_BANKS[pair] || 0;
    elCaption.textContent =
      pair + " " + DASH + " " + nBanks + (nBanks === 1 ? " bank" : " banker") +
      " med j\\u00e4mf\\u00f6rbar spread " + DASH + " " + cfg.name + ", " + viewTxt +
      ", " + outTxt + ", " + lb + " dagars f\\u00f6nster " + DASH +
      " j\\u00e4mf\\u00f6relsebank: " + (baseBank || DASH) +
      " " + DASH + " sorterad p\\u00e5 smalast spread";

    const cls = function (v) { return v === null ? "" : (v > 0 ? "up" : (v < 0 ? "down" : "")); };
    var anyApprox = false, anyStale = false, anyThin = false;

    elTbody.innerHTML = rows.map(function (r) {
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

    // Vilka banker som noterar paret ar i sig en upplysning nar listan gar
    // utanfor huvudvalutorna: en enda rad betyder att bara Swedbank har det.
    var coverage = "";
    if (rows.length === 1) {
      coverage = " Bara en bank noterar det h\\u00e4r paret, s\\u00e5 " +
                 "j\\u00e4mf\\u00f6relsekolumnerna \\u00e4r tomma.";
    }

    var meanNote = "";
    if (state.mean) {
      const bl = hasMean(pair) ? MEAN_BANKS[pair] : [];
      meanNote = bl.length
        ? " Den streckade linjen \\u00e4r dagens medelspread f\\u00f6r " +
          bl.join(", ") + "; det skuggade bandet \\u00e4r \\u00b1" +
          bandText() + " % av det snittet. Gruppen s\\u00e4tts per par ur de " +
          "banker som faktiskt noterar valutan. Swedbank ing\\u00e5r " +
          "inte, eftersom den \\u00e4r j\\u00e4mf\\u00f6relsebank i tabellen. " +
          "Saknar en bank en dag anv\\u00e4nds dess senaste niv\\u00e5 (h\\u00f6gst " +
          MEAN_FFILL_DAYS + " dagar) s\\u00e5 att snittet r\\u00e4knas p\\u00e5 samma " +
          "banker hela tiden \\u2013 h\\u00e5ll muspekaren \\u00f6ver linjen f\\u00f6r " +
          "att se hur m\\u00e5nga som hade en egen notering. Dagar med f\\u00e4rre \\u00e4n " +
          MEAN_MIN_BANKS + " banker utel\\u00e4mnas helt och \\u00f6verbryggas."
        : " Inget banksnitt f\\u00f6r det h\\u00e4r paret \\u2013 f\\u00e4rre \\u00e4n " +
          MEAN_MIN_BANKS + " banker utanf\\u00f6r Swedbank noterar valutan med " +
          "j\\u00e4mf\\u00f6rbar spread.";
    }

    const quote = pair.split("/")[1];
    const B = " \\u2022 ";   // punktavskiljare mellan definitioner

    // Stycke 1: vad kolumnerna ar.
    const defs =
      "1 pip = " + PIP_LABELS[pair] + " " + quote +
      // Avviker paret fran standardpipen ar det vart att saga varfor - annars
      // ser pipstalen ut att ligga en tiopotens fel mot ovriga par.
      (PIP_LABELS[pair] !== DEFAULT_PIP_LABEL
        ? " (finare \\u00e4n standardpipen " + DEFAULT_PIP_LABEL + ", eftersom " +
          pair.split("/")[0] + " noteras s\\u00e5 l\\u00e5gt per enhet)"
        : "") + B +
      "\\u0394 pips = f\\u00f6r\\u00e4ndringen i bankens p\\u00e5slag, i pips" + B +
      "\\u0394 marginal = f\\u00f6r\\u00e4ndringen i spread/mid, i % av den " +
      "TIDIGARE marginalen (0.40 % \\u2192 0.44 % ger +10.0)" + B +
      "Trend = p\\u00e5slagets lutning i pips per dag \\u00f6ver f\\u00f6nstret" + B +
      "F\\u00f6nstret styr delta-, trend- och j\\u00e4mf\\u00f6relsekolumnerna samt " +
      "trendlinjen i grafen" + B +
      "Positiva v\\u00e4rden = bredare spread \\u00e4n f\\u00f6rr / \\u00e4n " +
      "j\\u00e4mf\\u00f6relsebanken.";

    // Stycke 2: vad de sager tillsammans. Bada slutsatserna forutsatter att
    // kursen faktiskt rort sig under fonstret - annars ar bada matten nara
    // noll oavsett hur banken prissatter.
    const reading =
      "Har kursen r\\u00f6rt sig under f\\u00f6nstret g\\u00e5r m\\u00e5tten is\\u00e4r, " +
      "och skillnaden avsl\\u00f6jar prismodellen." + B +
      "\\u0394 pips n\\u00e4ra noll men \\u0394 marginal skild fr\\u00e5n noll: banken " +
      "h\\u00e5ller ett fast p\\u00e5slag i kronor, s\\u00e5 kundens relativa kostnad " +
      "f\\u00f6ljer kursen" + B +
      "\\u0394 marginal n\\u00e4ra noll men \\u0394 pips skild fr\\u00e5n noll: banken " +
      "priss\\u00e4tter i procent av kursen, s\\u00e5 p\\u00e5slaget i kronor f\\u00f6ljer " +
      "med upp och ner" + B +
      "Har kursen st\\u00e5tt stilla blir b\\u00e5da n\\u00e4ra noll, och d\\u00e5 g\\u00e5r " +
      "det inte att s\\u00e4ga vilken modell banken anv\\u00e4nder.";

    // Stycke 3: forbehall och symbolnoter - bara nar de faktiskt galler.
    var caveats =
      (anyApprox ? " <b>*</b> = kortare historik \\u00e4n " + lb +
                   " dagar; \\u00e4ldsta punkten anv\\u00e4nds." : "") +
      (anyStale ? " <b>\\u2020</b> = senaste observationen \\u00e4r \\u00e4ldre \\u00e4n " +
                  "\\u00f6vriga bankers." : "") +
      (anyThin ? " F\\u00e4rre \\u00e4n tre observationer i f\\u00f6nstret f\\u00f6r minst " +
                 "en bank \\u2013 trenden \\u00e4r d\\u00e5 mycket os\\u00e4ker." : "") +
      coverage + meanNote;

    elFoot.innerHTML =
      "<p>" + defs + "</p><p>" + reading + "</p>" +
      (caveats.trim() ? "<p>" + caveats.trim() + "</p>" : "");

    // Tabellen och fotnoten byter hojd har: ett par med fyra banker i stallet
    // for sex ger tva rader mindre, och ett forbehall extra kan lagga till en
    // rad text. Grafen far darfor rakna om sin andel av fonstret.
    fitThrottled();
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
    // i stallet for att lata den se trasig ut. Procentrutan graas dessutom ut
    // sa lange snittet ar avslaget, eftersom den da inte styr nagot synligt.
    const ok = hasMean(state.pair);
    document.querySelectorAll("#meanGroup button").forEach(function (b) {
      b.disabled = !ok;
    });
    elBand.disabled = !(ok && state.mean);
  }

  function rafThrottle(fn) {
    // En omrakning per ram, sa att ett snabbt drag eller en nedhallen
    // piltangent inte koar upp hundratals Plotly-anrop.
    var pending = false;
    return function () {
      if (pending) return;
      pending = true;
      window.requestAnimationFrame(function () { pending = false; fn(); });
    };
  }

  window.addEventListener("load", function () {
    cacheDom();

    // Byte av par styr synlighet separat fran ovriga kontroller.
    document.getElementById("pairSelect").addEventListener("change", function (e) {
      state.pair = e.target.value;
      syncMeanControl();
      render();
    });

    wire("unitGroup",    "unit",     function (v) { return v; });
    wire("viewGroup",    "view",     function (v) { return v; });
    wire("trendGroup",   "trend",    function (v) { return v === "on"; });
    wire("meanGroup",    "mean",     function (v) { return v === "on"; });
    wire("pointsGroup",  "points",   function (v) { return v; });
    wire("outlierGroup", "outliers", function (v) { return v; });

    // Snittknappen styr ocksa om procentrutan ar tillganglig.
    document.querySelectorAll("#meanGroup button").forEach(function (b) {
      b.addEventListener("click", syncMeanControl);
    });

    // --- Bandbredden -----------------------------------------------------
    // Rutan ror bara de tva kantlinjerna och tabellens fotnot; varken
    // serierna, axlarna eller sjalva snittlinjen berors.
    const applyBandThrottled = rafThrottle(function () {
      applyBand();
      renderTable();
    });

    elBand.addEventListener("input", function () {
      const v = parseFloat(elBand.value.replace(",", "."));
      const ok = isFinite(v) && v >= MEAN_BAND_MIN && v <= MEAN_BAND_MAX;
      // Ogiltig inmatning markeras men lamnar grafen orord, sa att en halvskriven
      // siffra ("1" pa vag mot "12") inte far bandet att hoppa till nagot vilt.
      elBand.classList.toggle("invalid", !ok);
      if (!ok) return;
      state.band = v;
      applyBandThrottled();
    });

    elBand.addEventListener("blur", function () {
      // Vid fokusslapp: stall tillbaka faltet till det varde som faktiskt ritas.
      elBand.value = bandText();
      elBand.classList.remove("invalid");
    });

    // --- Tillbakablicksfonstret -----------------------------------------
    const slider = document.getElementById("lookbackSlider");
    const output = document.getElementById("lookbackValue");
    const applyWindowThrottled = rafThrottle(applyWindow);

    function showValue() { output.textContent = slider.value + " d"; }

    slider.addEventListener("input", function () {
      state.lookback = +slider.value;
      showValue();                 // etiketten far svara direkt
      applyWindowThrottled();      // fonstret ror bara trend + tabell
    });

    showValue();
    syncMeanControl();

    // --- Storleken -------------------------------------------------------
    // Hojden kan andras av fonstret (resize / orientering) och av att
    // innehallet runt grafen byter hojd: kontrollraden radbryts, tabellen
    // byter antal rader, fotnoten far en rad forbehall till.
    window.addEventListener("resize", fitThrottled);
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", fitThrottled);
    }
    if (window.ResizeObserver) {
      // Textombrytning ger hojdandringar som ingen resize-handelse fangar -
      // observern ser dem oavsett vad som orsakade dem. renderTable anropar
      // dessutom fitThrottled sjalv, sa aven en webblasare utan
      // ResizeObserver haller sig ajour.
      const ro = new ResizeObserver(fitThrottled);
      [elControls, elTableWrap, elFoot].forEach(function (el) {
        if (el) ro.observe(el);
      });
    }

    render();
    fitPlot();
  });
</script>
</body>
</html>
"""

SUBS = {
    "__PAIR_OPTIONS__": pair_options_html,
    "__UNITS__": dump(UNITS),
    "__BASE_Y__": dump(baseY),
    "__KEEP_MASK__": dump(keepMask),
    "__SPIKE_MASK__": dump(spikeMask),
    "__SERIES_LEN__": dump(seriesLen),
    "__X_SEC__": dump(xSec),
    "__DAYS__": dump(dayStr),
    "__BANK_OF__": dump(bankOf),
    "__TRACE_POS__": dump(trace_pos),
    "__PAIR_POS__": dump(pair_pos),
    "__HOVER_TPL__": dump(HOVER_TPL),
    "__Y_TITLE__": dump({u: HOVER_UNIT[u][1] for u in UNITS}),
    "__DATA_IDX__": dump(data_idx),
    "__TREND_IDX__": dump(trend_idx),
    "__BANKDAY_BREAKS__": dump(bankday_breaks),
    "__TRACE_PAIRS__": dump(trace_pairs),
    "__TRACE_KIND__": dump(trace_kind),
    "__PRESENT_PAIRS__": dump(present_pairs),
    "__TABLE_BASELINE__": dump(table_baseline),
    "__PIP_LABELS__": dump(pip_labels),
    "__DEFAULT_PIP_LABEL__": dump(pip_label(DEFAULT_PIP_SIZE)),
    "__PAIR_BANKS__": dump(pair_bank_count),
    "__SESSION_DAYS__": dump(session_days),
    "__DAY_RANGE__": dump(day_range),
    "__MEAN_Y__": dump(meanY),
    "__MEAN_N__": dump(meanN),
    "__MEAN_HOVER__": dump(MEAN_HOVER),
    "__MEAN_IDX__": dump(mean_idx),
    "__BAND_LO_IDX__": dump(band_lo_idx),
    "__BAND_HI_IDX__": dump(band_hi_idx),
    "__MEAN_POS__": dump(mean_pos),
    "__MEAN_BANKS__": dump(mean_banks),
    "__MEAN_BAND_PCT__": dump(MEAN_BAND_PCT),
    "__MEAN_BAND_MIN__": dump(MEAN_BAND_MIN),
    "__MEAN_BAND_MAX__": dump(MEAN_BAND_MAX),
    "__MEAN_MIN_BANKS__": dump(MEAN_MIN_BANKS),
    "__MEAN_FFILL_DAYS__": dump(MEAN_FFILL_DAYS),
    "__BAND_PCT__": f"{MEAN_BAND_PCT:g}",
    "__BAND_MIN__": f"{MEAN_BAND_MIN:g}",
    "__BAND_MAX__": f"{MEAN_BAND_MAX:g}",
    "__BAND_STEP__": f"{MEAN_BAND_STEP:g}",
    "__MIN_LOOKBACK__": str(MIN_LOOKBACK),
    "__MAX_LOOKBACK__": str(max_lookback),
    "__DEFAULT_LOOKBACK__": dump(default_lookback),
    "__PLOT_MIN_HEIGHT__": dump(PLOT_MIN_HEIGHT),
    "__PLOT_BOTTOM_GAP__": dump(PLOT_BOTTOM_GAP),
    # Storst av alla - lags in i samma svep i stallet for att skannas om av
    # varje efterfoljande .replace() i en kedja.
    "__PLOT_DIV__": plot_div,
}

_missing = sorted(set(re.findall(r"__[A-Z_]+__", PAGE)) - set(SUBS))
if _missing:
    raise KeyError(f"Platshallare utan varde i SUBS: {', '.join(_missing)}")

# En genomgang av mallen. Utover att vara snabbare undviker det en latent bugg
# i replace-kedjan: en platshallare som rakade dyka upp i redan inlagd JSON
# eller i plot_div skulle ha substituerats av ett senare anrop.
page = re.sub(r"__[A-Z_]+__", lambda m: SUBS[m.group(0)], PAGE)

DOCS_DIR.mkdir(exist_ok=True)
out = DOCS_DIR / "index.html"
out.write_text(page, encoding="utf-8")
print(f"Skrev {out} \u2013 {len(present_pairs)} par, {len(banks)} banker, "
      f"{len(fig.data)} traces")