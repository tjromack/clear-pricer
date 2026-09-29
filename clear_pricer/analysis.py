"""Milestone 7 analysis: price variation for the same CPT code across three Chicago hospitals (CP-DEC 015).

    clear-pricer analysis                                  # from data/published/ (a local export)
    clear-pricer analysis --release data-2026-09-29-27a34004   # from a public release, over HTTPS

Writes docs/analysis/price-variation.md, docs/analysis/price-variation.json and docs/analysis/figures/*.png. Every number
in the document is computed here from the published Parquet -- nothing is typed by hand -- so the analysis is
reproducible by anyone from a pinned release with only DuckDB (and matplotlib for the figures).

What is compared, and why (the method section of the document explains each):
- CPT Category I codes, outpatient (setting 'outpatient' or 'both').
- Line-item prices only: Northwestern's case-package items (a LOCAL `CASE-` code) price whole surgical cases, not units.
- "Unlisted procedure" codes excluded: they are catch-alls, not comparable by definition.
- List (gross) and cash prices for all three hospitals; contracted prices only as fee-schedule dollars
  (rate_basis 'dollar' AND methodology 'fee schedule'), and only Rush vs UChicago -- Northwestern's published
  fee-schedule dollars are not usable as unit prices (e.g. $8.61 against a $21,050 list price).
- Per hospital x code, the median across rows; for contracted prices, the median across payers of each payer's median.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "analysis"
RELEASE_URL = "https://github.com/tjromack/clear-pricer/releases/download/{tag}"
HOSPITALS = {"nm": "Northwestern Memorial", "rush": "Rush University MC", "uchicago": "UChicago Medical Center"}
SHORT = {"nm": "Northwestern", "rush": "Rush", "uchicago": "UChicago"}  # chart legends/axes
# A basket of familiar outpatient services, fixed before looking at their prices. Labels are plain-English paraphrases.
BASKET = [
    ("99213", "Office visit, established patient (low complexity)"),
    ("99214", "Office visit, established patient (moderate complexity)"),
    ("85025", "Complete blood count (CBC) with differential"),
    ("80053", "Comprehensive metabolic panel"),
    ("71046", "Chest X-ray, 2 views"),
    ("70450", "CT head, without contrast"),
    ("72148", "MRI lumbar spine, without contrast"),
    ("74177", "CT abdomen & pelvis, with contrast"),
    ("45378", "Colonoscopy, diagnostic"),
]
CONTRACTED = "rate_basis = 'dollar' AND methodology = 'fee schedule'"

# light / dark chart tokens (dataviz reference palette; categorical slots 1-3 validated all-pairs in both modes)
THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
              "axis": "#c3c2b7", "series": ["#2a78d6", "#eb6834", "#1baf7a"], "seq": "#2a78d6"},
    "dark": {"surface": "#1a1a19", "ink": "#ffffff", "ink2": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a",
             "axis": "#383835", "series": ["#3987e5", "#d95926", "#199e70"], "seq": "#3987e5"},
}


def _connect(source: str) -> tuple[duckdb.DuckDBPyConnection, dict]:
    con = duckdb.connect()
    con.sql("SET enable_progress_bar = false")
    con.sql("SET threads = 1")  # deterministic aggregation order
    base = RELEASE_URL.format(tag=source) if not Path(source).exists() else Path(source).as_posix()
    for t in ("fct_standard_charges", "dim_charge_codes", "files"):
        con.sql(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{base}/{t}.parquet')")
    if Path(source).exists():
        manifest = json.loads((Path(source) / "manifest.json").read_text(encoding="utf-8"))
    else:
        import urllib.request

        with urllib.request.urlopen(f"{base}/manifest.json") as r:  # noqa: S310 -- fixed GitHub release URL
            manifest = json.loads(r.read().decode("utf-8"))
    # the comparable set, defined once
    con.sql("""
        CREATE TEMP TABLE rows AS
        WITH pkg AS (SELECT DISTINCT item_id FROM dim_charge_codes WHERE declared_type = 'LOCAL' AND code LIKE 'CASE-%'),
        codes AS (SELECT DISTINCT item_id, code FROM dim_charge_codes
                  WHERE code_family = 'CPT_CAT_I' AND item_id NOT IN (SELECT item_id FROM pkg))
        SELECT f.hospital_id, c.code, f.description, f.gross_charge, f.discounted_cash, f.negotiated_rate,
               f.rate_basis, f.methodology, f.payer_name
        FROM fct_standard_charges f JOIN codes c USING (item_id)
        WHERE f.setting IN ('outpatient', 'both') AND f.description NOT ILIKE '%unlisted%'
    """)
    con.sql("""
        CREATE TEMP TABLE per_code AS
        SELECT hospital_id, code, median(gross_charge) AS gross, median(discounted_cash) AS cash, count(*) AS n_rows
        FROM rows GROUP BY 1, 2
    """)
    con.sql(f"""
        CREATE TEMP TABLE payer_rates AS
        SELECT hospital_id, code, payer_name, median(negotiated_rate) AS rate
        FROM rows WHERE {CONTRACTED} AND hospital_id IN ('rush', 'uchicago') AND negotiated_rate > 0
        GROUP BY 1, 2, 3
    """)
    return con, manifest


def _q(con, sql: str) -> list[tuple]:
    return con.sql(sql).fetchall()


def compute(con) -> dict:
    r: dict = {}
    # 1. what kind of dollar is it? (all rows, all codes)
    r["dollar_kinds"] = _q(con, """
        SELECT hospital_id,
          CASE WHEN rate_basis = 'dollar' AND methodology = 'fee schedule' THEN 'contracted fee schedule'
               WHEN rate_basis = 'dollar' AND negotiated_rate = gross_charge THEN 'equals the list price'
               WHEN rate_basis = 'dollar' AND methodology IN ('case rate', 'per diem') THEN 'package (case rate / per diem)'
               WHEN rate_basis = 'dollar' THEN 'other contracted dollar (not fee schedule)'
               WHEN rate_basis = 'dollar_from_percent' THEN 'percent of list price'
               WHEN rate_basis = 'dollar_percent_unreconciled' THEN 'dollar and % disagree'
               ELSE 'no dollar published' END AS kind,
          count(*) AS n
        FROM fct_standard_charges GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2""")
    r["rush_list_equals_negotiated"] = _q(con, """
        SELECT count(*), count(DISTINCT payer_name) FROM fct_standard_charges
        WHERE hospital_id = 'rush' AND rate_basis = 'dollar' AND negotiated_rate = gross_charge
          AND methodology IS DISTINCT FROM 'fee schedule'""")[0]  # same definition as the table in section 1
    r["rush_list_equals_negotiated_payers"] = _q(con, """
        SELECT payer_name, count(*) FROM fct_standard_charges
        WHERE hospital_id = 'rush' AND rate_basis = 'dollar' AND negotiated_rate = gross_charge
          AND methodology IS DISTINCT FROM 'fee schedule'
        GROUP BY 1 HAVING count(*) > 1000 ORDER BY 1""")
    r["nm_fee_schedule"] = _q(con, """
        SELECT count(*), count(DISTINCT payer_name) FROM fct_standard_charges
        WHERE hospital_id = 'nm' AND rate_basis = 'dollar' AND methodology = 'fee schedule'""")[0]
    r["nm_fee_examples"] = _q(con, """
        SELECT description, negotiated_rate, gross_charge FROM (
            SELECT DISTINCT description, negotiated_rate, gross_charge FROM fct_standard_charges
            WHERE hospital_id = 'nm' AND rate_basis = 'dollar' AND methodology = 'fee schedule' AND gross_charge > 0)
        ORDER BY negotiated_rate / gross_charge, description LIMIT 3""")
    r["nm_case_items"] = _q(con, """
        SELECT count(DISTINCT item_id) FROM dim_charge_codes WHERE declared_type = 'LOCAL' AND code LIKE 'CASE-%'""")[0][0]

    # 2. list and cash prices across the three hospitals
    for m in ("gross", "cash"):
        r[m] = dict(zip(
            ("codes_all3", "p25", "median", "p75", "p90", "ge2x", "ge3x"),
            _q(con, f"""
                WITH x AS (SELECT code, max({m}) / min({m}) AS ratio FROM per_code WHERE {m} > 0
                           GROUP BY 1 HAVING count(*) = 3)
                SELECT count(*), quantile_cont(ratio, 0.25), median(ratio), quantile_cont(ratio, 0.75),
                       quantile_cont(ratio, 0.9), count(*) FILTER (WHERE ratio >= 2), count(*) FILTER (WHERE ratio >= 3)
                FROM x""")[0]))
        r[m]["rank"] = _q(con, f"""
            WITH x AS (SELECT *, rank() OVER (PARTITION BY code ORDER BY {m} DESC) AS rk, count(*) OVER (PARTITION BY code) AS h
                       FROM per_code WHERE {m} > 0)
            SELECT hospital_id, count(*) FILTER (WHERE rk = 1), count(*) FILTER (WHERE rk = 3)
            FROM x WHERE h = 3 GROUP BY 1 ORDER BY 1""")
    r["cash_policy"] = {h: {"median_cash_over_list": m, "share_cash_equals_list": e} for h, m, e in _q(con, """
        SELECT hospital_id, median(discounted_cash / gross_charge),
               avg(CASE WHEN discounted_cash = gross_charge THEN 1.0 ELSE 0.0 END)
        FROM rows WHERE gross_charge > 0 AND discounted_cash IS NOT NULL GROUP BY 1 ORDER BY 1""")}
    r["gross_ratios"] = [x for (x,) in _q(con, """
        SELECT max(gross) / min(gross) FROM per_code WHERE gross > 0 GROUP BY code HAVING count(*) = 3 ORDER BY 1""")]

    # 3. contracted fee-schedule prices: between Rush and UChicago, and across payers within each
    r["between"] = dict(zip(
        ("codes", "median_rush_over_uc", "p25", "p75", "rush_higher", "median_high_over_low"),
        _q(con, """
            WITH h AS (SELECT hospital_id, code, median(rate) AS med FROM payer_rates GROUP BY 1, 2),
            x AS (SELECT code, max(med) FILTER (WHERE hospital_id = 'rush') AS rush,
                         max(med) FILTER (WHERE hospital_id = 'uchicago') AS uc
                  FROM h GROUP BY 1 HAVING count(*) = 2)
            SELECT count(*), median(rush / uc), quantile_cont(rush / uc, 0.25), quantile_cont(rush / uc, 0.75),
                   count(*) FILTER (WHERE rush > uc), median(greatest(rush, uc) / least(rush, uc)) FROM x""")[0]))
    r["within"] = {h: dict(zip(("codes", "payers", "median_spread", "p90_spread"), row)) for h, *row in _q(con, """
        WITH s AS (SELECT hospital_id, code, count(*) AS n, max(rate) / min(rate) AS spread FROM payer_rates
                   GROUP BY 1, 2 HAVING count(*) >= 3)
        SELECT hospital_id, count(*), (SELECT count(DISTINCT payer_name) FROM payer_rates p WHERE p.hospital_id = s.hospital_id),
               median(spread), quantile_cont(spread, 0.9) FROM s GROUP BY 1 ORDER BY 1""")}

    # 4. the basket
    codes = ", ".join(f"'{c}'" for c, _ in BASKET)
    rows = _q(con, f"""
        WITH c AS (SELECT hospital_id, code, median(rate) AS contracted, count(*) AS payers FROM payer_rates
                   WHERE code IN ({codes}) GROUP BY 1, 2)
        SELECT p.code, p.hospital_id, p.gross, p.cash, c.contracted, c.payers
        FROM per_code p LEFT JOIN c USING (hospital_id, code) WHERE p.code IN ({codes}) ORDER BY 1, 2""")
    r["basket"] = {code: {h: {"gross": g, "cash": ca, "contracted": co, "payers": pa}
                          for cd, h, g, ca, co, pa in rows if cd == code} for code, _ in BASKET}
    r["mri_payers"] = _q(con, """
        SELECT hospital_id, payer_name, rate FROM payer_rates WHERE code = '72148' ORDER BY hospital_id, rate, payer_name""")
    return r


# --- figures ---------------------------------------------------------------------------------------------------------

def _style(ax, t: dict) -> None:
    ax.set_facecolor(t["surface"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(t["axis"])
    ax.tick_params(colors=t["muted"], labelsize=9, length=0)
    ax.grid(axis="y", color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)


def figures(r: dict, out: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    out.mkdir(parents=True, exist_ok=True)
    written = []
    for mode, t in THEMES.items():
        plt.rcParams.update({"font.family": "DejaVu Sans", "svg.hashsalt": "clear-pricer"})

        # Figure 1: distribution of the list-price ratio (single series, no legend)
        bins = [1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5, 7.5, 10, 1e9]
        labels = ["1–1.25", "1.25–1.5", "1.5–1.75", "1.75–2", "2–2.5", "2.5–3", "3–4", "4–5", "5–7.5", "7.5–10", "10+"]
        counts = [sum(1 for x in r["gross_ratios"] if lo <= x < hi) for lo, hi in zip(bins, bins[1:])]
        fig, ax = plt.subplots(figsize=(8, 3.6), dpi=150)
        fig.patch.set_facecolor(t["surface"])
        _style(ax, t)
        for i, c in enumerate(counts):  # bars with a rounded data-end anchored to the baseline, 2px surface gaps
            ax.add_patch(FancyBboxPatch((i - 0.4, 0), 0.8, c, boxstyle="round,pad=0,rounding_size=0.06",
                                        mutation_aspect=max(counts) / 12, facecolor=t["seq"], edgecolor=t["surface"],
                                        linewidth=1.5))
        ax.set_xlim(-0.6, len(counts) - 0.4)
        ax.set_ylim(0, max(counts) * 1.12)
        ax.set_xticks(range(len(labels)), labels)
        ax.set_xlabel("Highest list price ÷ lowest list price, same code", color=t["ink2"], fontsize=9)
        ax.set_ylabel("CPT codes", color=t["ink2"], fontsize=9)
        med = r["gross"]["median"]
        med_bin = next(i for i, (lo, hi) in enumerate(zip(bins, bins[1:])) if lo <= med < hi)
        ax.annotate(f"median {med:.2f}×", xy=(med_bin, counts[med_bin]), xytext=(0, 6), textcoords="offset points",
                    ha="center", color=t["ink"], fontsize=9, fontweight="bold")
        ax.set_title(f"List price for the same service varies a median {med:.1f}× across three Chicago hospitals",
                     loc="left", color=t["ink"], fontsize=10.5, pad=10)
        fig.tight_layout()
        p = out / f"list-price-ratio-{mode}.png"
        fig.savefig(p, facecolor=t["surface"], metadata={"Software": None})
        plt.close(fig)
        written.append(p.name)

        # Figure 2: the basket -- list price by hospital (3-series dot plot, legend + table view in the doc)
        fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
        fig.patch.set_facecolor(t["surface"])
        _style(ax, t)
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color=t["grid"], linewidth=0.8)
        rows = [(code, label) for code, label in BASKET if len(r["basket"].get(code, {})) >= 2]
        for yi, (code, label) in enumerate(rows):
            vals = [r["basket"][code].get(h, {}).get("gross") for h in HOSPITALS]
            present = [v for v in vals if v]
            ax.plot([min(present), max(present)], [yi, yi], color=t["axis"], linewidth=2, zorder=1)
            for si, (h, v) in enumerate(zip(HOSPITALS, vals)):
                if v:  # small vertical dodge per hospital: near-equal prices would otherwise hide each other
                    ax.scatter(v, yi + (si - 1) * 0.16, s=56, color=t["series"][si], edgecolors=t["surface"],
                               linewidths=2, zorder=3, label=SHORT[h] if yi == 0 else None)
        ax.set_xscale("log")
        ax.set_yticks(range(len(rows)), [f"{lab}  ({code})" for code, lab in rows], color=t["ink2"], fontsize=8.5)
        ax.invert_yaxis()
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
        ax.set_xlabel("List (gross) price, median across line items — log scale", color=t["ink2"], fontsize=9)
        leg = ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, frameon=False, fontsize=8.5,
                        labelcolor=t["ink2"], handletextpad=0.3, columnspacing=1.2, borderaxespad=0.2)
        ax.set_title("Same service, three list prices", loc="left", color=t["ink"], fontsize=10.5, pad=26)
        fig.tight_layout()
        p = out / f"basket-list-prices-{mode}.png"
        fig.savefig(p, facecolor=t["surface"], metadata={"Software": None})
        plt.close(fig)
        written.append(p.name)
        del leg

        # Figure 3: MRI lumbar spine -- contracted fee-schedule rate by payer, per hospital (strip)
        fig, ax = plt.subplots(figsize=(8, 2.8), dpi=150)
        fig.patch.set_facecolor(t["surface"])
        _style(ax, t)
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color=t["grid"], linewidth=0.8)
        hosp = [h for h in ("rush", "uchicago") if any(x[0] == h for x in r["mri_payers"])]
        for yi, h in enumerate(hosp):
            pts = [(payer, rate) for hh, payer, rate in r["mri_payers"] if hh == h]
            color = t["series"][list(HOSPITALS).index(h)]
            ax.scatter([rate for _, rate in pts], [yi] * len(pts), s=64, color=color, edgecolors=t["surface"],
                       linewidths=2, zorder=3)
            for payer, rate in (pts[0], pts[-1]):  # selective direct labels: the extremes only
                ax.annotate(f"{payer}\n${rate:,.0f}", xy=(rate, yi), xytext=(0, 9), textcoords="offset points",
                            ha="center", va="bottom", color=t["ink2"], fontsize=7.5)
        ax.set_yticks(range(len(hosp)), [SHORT[h] for h in hosp], color=t["ink2"], fontsize=9)
        ax.set_ylim(-0.6, len(hosp) - 0.2)
        ax.invert_yaxis()
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
        ax.set_xlabel("Contracted fee-schedule rate for MRI lumbar spine (72148), one dot per payer", color=t["ink2"],
                      fontsize=9)
        ax.set_title("Who pays moves the price more than where you go", loc="left", color=t["ink"], fontsize=10.5,
                     pad=10)
        fig.tight_layout()
        p = out / f"mri-payers-{mode}.png"
        fig.savefig(p, facecolor=t["surface"], metadata={"Software": None})
        plt.close(fig)
        written.append(p.name)
    return written


# --- document --------------------------------------------------------------------------------------------------------

def _money(v: float | None) -> str:
    return "—" if v is None else f"${v:,.0f}" if v >= 100 else f"${v:,.2f}"


def _contracted(cell: dict) -> str:
    v, n = cell.get("contracted"), cell.get("payers")
    return "—" if v is None else f"{_money(v)} ({n} payer{'s' if n != 1 else ''})"


def _pic(name: str, alt: str) -> str:
    return (f'<picture><source media="(prefers-color-scheme: dark)" srcset="figures/{name}-dark.png">'
            f'<img alt="{alt}" src="figures/{name}-light.png" width="720"></picture>')


def render(r: dict, manifest: dict, source_label: str, reproduce: str) -> str:
    g, c, b, w = r["gross"], r["cash"], r["between"], r["within"]
    fp = manifest["fingerprint"]
    kinds: dict[str, dict[str, int]] = {}
    for h, kind, n in r["dollar_kinds"]:
        kinds.setdefault(h, {})[kind] = n
    kind_order = ["contracted fee schedule", "package (case rate / per diem)", "equals the list price",
                  "other contracted dollar (not fee schedule)",
                  "percent of list price", "dollar and % disagree", "no dollar published"]
    totals = {h: sum(v.values()) for h, v in kinds.items()}
    kind_rows = "\n".join(
        f"| {k} | " + " | ".join(f"{kinds[h].get(k, 0):,} ({100 * kinds[h].get(k, 0) / totals[h]:.1f}%)" for h in HOSPITALS)
        + " |" for k in kind_order)
    rank = lambda m: "; ".join(f"{HOSPITALS[h]} highest on {hi:,}, lowest on {lo:,}" for h, hi, lo in r[m]["rank"])  # noqa: E731
    basket_rows = "\n".join(
        f"| {label} (`{code}`) | " + " | ".join(_money(r["basket"][code].get(h, {}).get("gross")) for h in HOSPITALS)
        + " | " + " | ".join(_money(r["basket"][code].get(h, {}).get("cash")) for h in HOSPITALS)
        + " | " + " | ".join(_contracted(r["basket"][code].get(h, {})) for h in ("rush", "uchicago")) + " |"
        for code, label in BASKET)
    mri = [(h, p, rate) for h, p, rate in r["mri_payers"]]
    mri_uc = [x for x in mri if x[0] == "uchicago"]
    mri_rows = "\n".join(f"| {HOSPITALS[h]} | {p} | {_money(rate)} |" for h, p, rate in mri)
    uc, ru = w.get("uchicago", {}), w.get("rush", {})
    mri_sentence = (f" An MRI of the lumbar spine at UChicago: **{_money(mri_uc[0][2])}** ({mri_uc[0][1]}) to "
                    f"**{_money(mri_uc[-1][2])}** ({mri_uc[-1][1]})." if len(mri_uc) >= 2 else "")
    lower_or_higher = "lower" if b["median_rush_over_uc"] < 1 else "higher"
    nm_ex = "; ".join(f"{d[:48]} — **{_money(n)}** against a list price of {_money(gr)}" for d, n, gr in r["nm_fee_examples"])
    rush_eq_n, rush_eq_payers = r["rush_list_equals_negotiated"]
    ma = r["rush_list_equals_negotiated_payers"]
    ma_n = sum(n for _, n in ma)
    ma_rows = "/".join(sorted({f"{n:,}" for _, n in ma}))
    cash_policy = "; ".join(
        f"{HOSPITALS[h]}'s cash price is a median {100 * v['median_cash_over_list']:.0f}% of its list price"
        + (f" (identical to it on {100 * v['share_cash_equals_list']:.0f}% of rows)" if v["share_cash_equals_list"] >= 0.5
           else "") for h, v in r["cash_policy"].items())

    return f"""# Same code, different price: three Chicago hospitals

*A clear-pricer analysis. Every number below is computed by `clear-pricer analysis` from {source_label}
(fingerprint `{fp[:16]}…`) — nothing is typed by hand; re-running reproduces this page.*

## Summary

- **List prices diverge.** For {g["codes_all3"]:,} outpatient CPT services priced by all three hospitals, the highest
  list (gross) price is a median **{g["median"]:.2f}×** the lowest; for one in ten codes it is **{g["p90"]:.1f}×** or more.
- **Cash prices diverge more:** a median **{c["median"]:.2f}×** across the same {c["codes_all3"]:,} codes.
- **For insured patients, who pays moves the price more than where they go.** Across {b["codes"]:,} services with
  contracted fee-schedule prices at both Rush and UChicago, the typical gap between the two hospitals is
  **{b["median_high_over_low"]:.2f}×** — but inside UChicago alone, the same service varies a median
  **{uc.get("median_spread", 0):.1f}×** across its {uc.get("payers", 0)} payers.{mri_sentence}
- **Most "negotiated prices" in these files are not comparable prices.** Only a minority of published dollars are
  per-service contracted rates: Rush lists {rush_eq_n:,} rates *at the full list price*, almost all for Medicare
  Advantage plans;
  Northwestern's dollars are overwhelmingly percentages of list price, packages, or figures that don't reconcile.
  The comparison below is built on the subset that is genuinely like-for-like, and says so.

{_pic("list-price-ratio", "Histogram of highest-to-lowest list price ratio across three hospitals")}

## 1. What can be compared at all

A price file's "negotiated rate" column mixes several different things. Only per-service contracted rates
(`fee schedule` methodology, a dollar amount) are the same unit across hospitals. Charge rows by what the dollar
actually is:

| What the dollar is | Northwestern | Rush | UChicago |
|---|---|---|---|
{kind_rows}

Three consequences shape everything below:

1. **Rush lists {rush_eq_n:,} rates at exactly the chargemaster price.** {ma_n:,} of them ({100 * ma_n / max(rush_eq_n, 1):.0f}%)
   belong to {len(ma)} Medicare Advantage plans with {ma_rows} rows each ({", ".join(p for p, _ in ma)}); the rest are
   scattered across {rush_eq_payers - len(ma)} other payers. A negotiated rate equal to the list price is not a
   negotiated price; these rows are excluded from contracted comparisons.
2. **Northwestern publishes only {r["nm_fee_schedule"][0]:,} fee-schedule dollars ({r["nm_fee_schedule"][1]} payers), and
   they don't behave like unit prices:** {nm_ex or "no examples in this release"}. With no way to tell valid rows from artifacts inside the file,
   Northwestern is excluded from the contracted comparison (it stays in the list- and cash-price comparisons).
3. **Northwestern prices {r["nm_case_items"]:,} whole surgical cases as items** (a local `CASE-` code). A case price
   is not a unit price, so those items are excluded everywhere below.

## 2. List prices: the same service, three prices

{g["codes_all3"]:,} outpatient CPT codes carry a list (gross) price at all three hospitals. The ratio of the highest to
the lowest: p25 **{g["p25"]:.2f}×** · median **{g["median"]:.2f}×** · p75 **{g["p75"]:.2f}×** · p90 **{g["p90"]:.2f}×**.
{g["ge2x"]:,} codes ({100 * g["ge2x"] / g["codes_all3"]:.0f}%) differ by 2× or more; {g["ge3x"]:,}
({100 * g["ge3x"] / g["codes_all3"]:.0f}%) by 3× or more. No hospital is uniformly expensive: {rank("gross")}.

{_pic("basket-list-prices", "Dot plot of list prices for nine familiar services at three hospitals")}

| Service | List: NM | List: Rush | List: UChicago | Cash: NM | Cash: Rush | Cash: UChicago | Contracted: Rush | Contracted: UChicago |
|---|---|---|---|---|---|---|---|---|
{basket_rows}

*Contracted = median across payers of each payer's fee-schedule rate. "—" = not published as a comparable price.*

## 3. Cash prices

The discounted cash price — what the hospital says a self-paying patient is charged — varies more than the list
price: across the same {c["codes_all3"]:,} codes, p25 **{c["p25"]:.2f}×** · median **{c["median"]:.2f}×** · p75
**{c["p75"]:.2f}×** · p90 **{c["p90"]:.2f}×**. {rank("cash")}.

Much of that is policy rather than price: the hospitals discount cash very differently. {cash_policy}. A
self-paying patient's price depends as much on the hospital's cash policy as on its list price.

## 4. Contracted prices: between hospitals vs. between payers

**Between hospitals.** For {b["codes"]:,} services with a fee-schedule rate at both Rush and UChicago, Rush's
payer-median rate is a median **{b["median_rush_over_uc"]:.2f}×** UChicago's (IQR {b["p25"]:.2f}–{b["p75"]:.2f}) —
Rush is {lower_or_higher} on most codes (higher on {b["rush_higher"]:,} of {b["codes"]:,}). The typical gap, whichever
side is higher: **{b["median_high_over_low"]:.2f}×**.

**Between payers, inside one hospital.** For services with at least three payers' rates:

| Hospital | Codes | Payers | Median spread (highest ÷ lowest payer) | 90th percentile |
|---|---|---|---|---|
| UChicago | {uc.get("codes", 0):,} | {uc.get("payers", 0)} | **{uc.get("median_spread", 0):.2f}×** | {uc.get("p90_spread", 0):.2f}× |
| Rush | {ru.get("codes", 0):,} | {ru.get("payers", 0)} | **{ru.get("median_spread", 0):.2f}×** | {ru.get("p90_spread", 0):.2f}× |

At UChicago, the payer a patient has moves the price of a typical service further than the choice between UChicago and
Rush does. Rush's contracts are much flatter across its payers — but its tail is as long as UChicago's.

{_pic("mri-payers", "Strip plot of contracted MRI lumbar spine rates by payer at Rush and UChicago")}

| Hospital | Payer | Contracted rate, MRI lumbar spine (72148) |
|---|---|---|
{mri_rows}

## Method

- **Source:** the clear-pricer data release named above: CMS machine-readable price files (template v3.0.0) for
  Northwestern Memorial, Rush University Medical Center and the University of Chicago Medical Center, parsed and gated
  by the pipeline (`DECISIONS.md` CP-DEC 005–008). Input file hashes are pinned in the release `manifest.json`.
- **Codes:** CPT Category I only, by *derived* code family (CP-DEC 006) — so CPT codes a hospital typed as `HCPCS` are
  included. Drug and supply codes (HCPCS Level II) are excluded: their units differ across files.
- **Setting:** outpatient, counting rows marked `both`.
- **Exclusions:** Northwestern's case-package items (`CASE-` local code); descriptions containing "unlisted".
- **Per hospital × code:** the median across its rows (a code often has several line items or payer rows). Contracted:
  the median across payers of each payer's median fee-schedule rate.
- **Ratios:** highest ÷ lowest across the hospitals that price the code (all three for list and cash; Rush and UChicago
  for contracted). Medians and percentiles are over codes, unweighted.

## Caveats

- **Three hospitals are not "the Chicago metro."** This is v1 of clear-pricer; the comparison is what these three files
  support. v2 extends it.
- **Prices, not volumes.** Every code counts once; a rarely billed code weighs as much as an office visit. No claims
  data is used.
- **List and cash prices are not what most patients pay.** Insured patients pay the contracted rate (plus their
  cost-sharing); list prices matter for uninsured patients and as the base that percentage contracts multiply.
- **Line items are not identical across hospitals.** One code can bundle different supplies, professional vs. facility
  components, or modifiers; medians reduce but do not remove this.
- **Publish dates differ:** Northwestern and UChicago published their files on 2026-04-01; Rush on 2026-09-25.
- **Northwestern's contracted prices are not compared**, for the reasons in section 1 — a limit of its file, stated
  rather than papered over.

## Reproduce

```bash
pip install -e ".[analysis]"   # duckdb + matplotlib
{reproduce}
```

The same numbers come out on any machine: the release is byte-reproducible (`DECISIONS.md` CP-DEC 014), and this
analysis reads it single-threaded in a fixed order.
"""


def run(source: str, out: Path = OUT) -> dict:
    con, manifest = _connect(source)
    r = compute(con)
    con.close()
    label = (f"the public data release [`{source}`](https://github.com/tjromack/clear-pricer/releases/tag/{source})"
             if not Path(source).exists() else "a local export of the gated warehouse")
    reproduce = (f"clear-pricer analysis --release {source}" if not Path(source).exists()
                 else "clear-pricer analysis --release <tag>   # any data release; or no flag for a local export")
    out.mkdir(parents=True, exist_ok=True)
    figures(r, out / "figures")
    (out / "price-variation.md").write_text(render(r, manifest, label, reproduce), encoding="utf-8", newline="\n")
    (out / "price-variation.json").write_text(
        json.dumps({"fingerprint": manifest["fingerprint"], "results": r}, indent=2, default=str),
        encoding="utf-8", newline="\n")
    return r
