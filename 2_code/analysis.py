"""
Wildfire damage, exposure to the fire lines and insurance violations of US property and
casualty insurers, 2013-2024.

Reads the files in ../1_data and writes every table and figure to ../output.

    python analysis.py                 full run
    python analysis.py --draws 100     fewer placebo draws for Figure 1 (default 1000)
    python analysis.py --draws 0       no new placebo draws; reuse output/figure1_*

Python 3.12 with pandas 3.0.6, numpy 2.5.3, scipy 1.18.1, pyfixest 0.60.0, openpyxl 3.1.5
and matplotlib. With 1,000 draws the run takes about five minutes on a laptop.
"""
import argparse
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyfixest as pf
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter
from scipy import stats

warnings.filterwarnings("ignore")

LINES = ["fire4", "fireonly", "firehome", "firebroad", "prop5", "privflood", "crop"]
HAZARDS = ["fire", "all", "flood", "tropical", "convective", "wind", "winter", "drought_heat"]
FIRST_YEAR = 2014       # 2013 market totals miss the unaffiliated companies
LAST_MS_YEAR = 2023     # the industry file has no group rows for 2024
LAST_VIOL_YEAR = 2024   # penalties of 2025 are only partly filed

FE = {
    "F1": ["year"],
    "F2": ["identifier", "year"],
    "F3": ["identifier", "state_postal", "year"],
    "F4": ["iy"],
    "F5": ["iy", "state_postal"],
    "F6": ["identifier", "sy"],
    "F7": ["ist", "sy"],
    "F8": ["iy", "sy"],
    "F9": ["ist", "iy"],
    "F10": ["ist", "iy", "sy"],
    "F11": ["ist", "year"],
}
FE_LABEL = {"F1": "Year", "F2": "Insurer + Year", "F3": "Insurer + State + Year",
            "F4": "Insurer × Year", "F5": "Insurer × Year + State",
            "F6": "Insurer + State × Year", "F7": "Insurer × State + State × Year",
            "F8": "Insurer × Year + State × Year", "F9": "Insurer × State + Insurer × Year",
            "F10": "Insurer × State + Insurer × Year + State × Year",
            "F11": "Insurer × State + Year"}
MAIN_FE = ["F3", "F5", "F7"]
MOD_FE = ["F7", "F9", "F10"]
DVS = ["count", "lnpen", "any"]
CLUSTER = {"insurer": "identifier", "state": "state_postal", "twoway": "identifier+state_postal"}

PAIRS = [("fire4", "fire"), ("fireonly", "fire"), ("firehome", "fire"), ("firebroad", "fire"),
         ("prop5", "tropical"), ("prop5", "convective"), ("prop5", "wind"), ("prop5", "winter"),
         ("privflood", "flood"), ("crop", "drought_heat"), ("prop5", "all")]
PLACEBO_HAZARDS = ["winter", "convective", "tropical", "flood"]
LOSS_PAIRS = [("fire4", "fire"), ("prop5", "tropical"), ("prop5", "convective"), ("prop5", "wind"),
              ("prop5", "winter"), ("prop5", "all")]
# wildfire damage is zero in most state-years, so the median dummy equals the any-damage dummy
DUMMY_MEASURES = ["lndmg", "dmgany", "dmgp75", "d_dmgp75"]

MODERATORS = {
    "low_state": "state", "low_is": "insurer_state",
    "tight_reg_free": "state", "tight_reg_free_rc": "state",
    "tight_reg_rate": "state", "tight_home": "state",
    "fewins_fire4_first": "state", "hitop3_fire4_first": "state", "hitop5_fire4_first": "state",
    "fewins_fire4_l1": "state", "hitop3_fire4_l1": "state", "hitop5_fire4_l1": "state",
    "fewins_fire4_t": "state", "hitop3_fire4_t": "state",
}
CONTROLS = {"none": [], "size": ["lnsize"], "size_hhi": ["lnsize", "hhi_{group}_t"]}
SPEC_KEYS = ["table", "panel", "expo", "group", "hazard", "damage", "dv", "horizon", "outcome",
             "fe", "design", "controls", "cluster", "moderator", "sample"]


# ----------------------------------------------------------------------------- data

def read_master(data):
    cols = (["identifier", "id_ins", "year", "state_postal", "territory", "premiums_written",
             "premiums_earned", "losses_incurred", "CPI"] + [f"lg_{g}" for g in LINES + ["fedflood"]])
    return pd.read_csv(data / "insurer_line_state_year.zip", usecols=cols)


def concentration(premiums):
    v = np.sort(premiums)[::-1]
    total = v.sum()
    if total <= 0:
        return pd.Series({"nins": 0, "top3": np.nan, "top5": np.nan, "hhi": np.nan})
    return pd.Series({"nins": len(v), "top3": v[:3].sum() / total,
                      "top5": v[:5].sum() / total, "hhi": float(((v / total) ** 2).sum() * 1e4)})


def market_structure(master):
    """Number of insurers, top-3 and top-5 premium shares and HHI by state, year and line group,
    over all insurers writing positive premiums."""
    m = master[master["territory"] == 0]
    pw = m["premiums_written"].fillna(0).clip(lower=0)
    parts = []
    for g in LINES:
        sel = m[f"lg_{g}"] == 1
        part = pd.DataFrame({"state_postal": m.loc[sel, "state_postal"], "year": m.loc[sel, "year"],
                             "identifier": m.loc[sel, "identifier"], "group": g, "pw": pw[sel]})
        parts.append(part.groupby(["state_postal", "year", "identifier", "group"], as_index=False)["pw"].sum())
    firm = pd.concat(parts, ignore_index=True)
    firm = firm.groupby(["state_postal", "year", "identifier", "group"], as_index=False)["pw"].sum()
    firm = firm[firm["pw"] > 0]
    ms = firm.groupby(["state_postal", "year", "group"])["pw"].apply(concentration).unstack().reset_index()
    ms["nins"] = ms["nins"].astype(int)
    wide = ms.pivot(index=["state_postal", "year"], columns="group")
    wide.columns = [f"{stat}_{g}" for stat, g in wide.columns]
    return wide.reset_index().sort_values(["state_postal", "year"])


def offense_outcomes(records):
    """Violation counts and penalties by insurer, state and penalty year, for insurance
    violations and for all other offense types, shifted to t+1, t+2 and t+3."""
    rec = records[records["in_state_panel"] == 1].copy()
    rec["non"] = 1 - rec["ins_viol"]
    rec["pen"] = rec["penalty_adjusted"].fillna(0)
    out = []
    for key, flag in [("ins", "ins_viol"), ("non", "non")]:
        sel = rec[rec[flag] == 1]
        agg = (sel.groupby(["id_ins", "state", "penalty_year"])
               .agg(**{f"count_{key}": ("pen", "size"), f"pen_{key}": ("pen", "sum")})
               .reset_index().rename(columns={"state": "state_postal"}))
        out.append(agg)
    v = out[0].merge(out[1], on=["id_ins", "state_postal", "penalty_year"], how="outer").fillna(0)
    merged = None
    for h in (1, 2, 3):
        f = v.assign(year=v["penalty_year"] - h).drop(columns="penalty_year")
        f = f.rename(columns={c: f"{c}_f{h}" for c in f.columns if c.startswith(("count_", "pen_"))})
        merged = f if merged is None else merged.merge(f, on=["id_ins", "state_postal", "year"], how="outer")
    return merged


def damage_measures(panel):
    """Change, lag and dummies of state-year damage. Percentiles are taken over state-years,
    zeros included."""
    sy = panel.drop_duplicates(["state_postal", "year"])[
        ["state_postal", "year"] + [f"lndmg_{h}" for h in HAZARDS]].copy()
    sy = sy.sort_values(["state_postal", "year"])
    notes = []
    for h in HAZARDS:
        col = f"lndmg_{h}"
        x = sy[col]
        lag = sy.groupby("state_postal")[col].shift(1)
        sy[f"lndmg_l1_{h}"] = lag
        sy[f"dlndmg_{h}"] = x - lag
        p50, p75, p90 = x.quantile(0.50), x.quantile(0.75), x.quantile(0.90)
        sy[f"dmgany_{h}"] = (x > 0).astype(float)
        sy[f"dmgp50_{h}"] = (x > p50).astype(float)
        sy[f"dmgp75_{h}"] = (x > p75).astype(float)
        d = sy[f"dlndmg_{h}"].dropna()
        notes.append({"hazard": h, "share_zero_level": float((x == 0).mean()),
                      "share_zero_change": float((d == 0).mean()),
                      "p50": float(p50), "p75": float(p75), "p75_usd_million": float(np.expm1(p75)),
                      "p50_equals_any": bool(p50 == 0), "p90": float(p90),
                      "p90_usd_million": float(np.expm1(p90))})
    for h in HAZARDS:
        for v in ["dmgany", "dmgp75"]:
            sy[f"d_{v}_{h}"] = sy[f"{v}_{h}"] - sy.groupby("state_postal")[f"{v}_{h}"].shift(1)

    x = sy["lndmg_fire"]
    sy["dmgp90_fire"] = (x > x.quantile(0.90)).astype(float)
    sy["dmg10m_fire"] = (x > np.log1p(10.0)).astype(float)
    sy["dmg100m_fire"] = (x > np.log1p(100.0)).astype(float)

    cols = [c for c in sy.columns if c.startswith(("dmgany_", "dmgp50_", "dmgp75_", "dmgp90_", "dmg10m_",
                                                   "dmg100m_", "d_dmg", "dlndmg_", "lndmg_l1_"))]
    return sy[["state_postal", "year"] + cols], pd.DataFrame(notes)


def dollars(s):
    s = str(s).strip()
    if s in ("", "nan"):
        return 0.0
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get(s[-1].upper())
    return float(s[:-1] or 0) * mult if mult else float(s)


# Reading the narratives of every flagged record, one match is wrong: this September 2014
# record is the Happy Camp Complex, matched to the Beaver Fire, a different California fire
# whose August record happens to carry the same USD 600,000.
WRONG_MATCH = {"531781"}


def repeated_records(fires, same_zone=True):
    """Storm Events splits a fire that burns into a new month into one record per month. Some
    offices repeat the fire's whole damage in every monthly record. A record is a repeat when it
    starts on the first day of a month and an earlier record that ends in the previous month
    carries the same property (or crop) damage; the repeated amount is then counted once.

    The earlier record must be in the same forecast zone, which makes the rule a lower bound: a
    fire whose record moves to another zone is not caught. `same_zone=False` drops that condition
    and is reported beside the main figure."""
    f = fires.copy()
    f["prop"] = f["DAMAGE_PROPERTY"].map(dollars)
    f["crop"] = f["DAMAGE_CROPS"].map(dollars)
    ym = f["BEGIN_YEARMONTH"].astype(int)
    f["begin_m"] = (ym // 100) * 12 + ym % 100
    ym = f["END_YEARMONTH"].astype(int)
    f["end_m"] = (ym // 100) * 12 + ym % 100
    rows = []
    for _, r in f[f["BEGIN_DAY"].astype(int) == 1].iterrows():
        if r["EVENT_ID"] in WRONG_MATCH:
            continue
        prev = f[(f["STATE"] == r["STATE"]) & (f["end_m"] == r["begin_m"] - 1)]
        if same_zone:
            prev = prev[(prev["CZ_TYPE"] == r["CZ_TYPE"]) & (prev["CZ_FIPS"] == r["CZ_FIPS"])]
        rep_prop = r["prop"] > 0 and (prev["prop"] == r["prop"]).any()
        rep_crop = r["crop"] > 0 and (prev["crop"] == r["crop"]).any()
        if rep_prop or rep_crop:
            rows.append({"state": r["STATE"], "year": int(r["YEAR"]), "event_id": r["EVENT_ID"],
                         "begin": r["BEGIN_DATE_TIME"], "end": r["END_DATE_TIME"], "zone": r["CZ_NAME"],
                         "property": r["prop"], "crops": r["crop"],
                         "repeated": r["prop"] * rep_prop + r["crop"] * rep_crop,
                         "narrative": r["EVENT_NARRATIVE"] or r["EPISODE_NARRATIVE"]})
    return pd.DataFrame(rows)


def corrected_damage(panel, fires, cpi):
    """Wildfire damage with the repeated monthly records counted once."""
    f = fires.copy()
    f["dmg"] = f["DAMAGE_PROPERTY"].map(dollars) + f["DAMAGE_CROPS"].map(dollars)
    names = panel.drop_duplicates("state_postal").set_index("state_name_uc")["state_postal"]
    f["state_postal"] = f["STATE"].map(names)
    f["year"] = f["YEAR"].astype(int)
    sy = panel.drop_duplicates(["state_postal", "year"])[
        ["state_postal", "year", "dmgnom_fire", "dmg24_fire", "lndmg_fire"]].copy()
    tot = f.dropna(subset=["state_postal"]).groupby(["state_postal", "year"])["dmg"].sum()
    chk = sy.set_index(["state_postal", "year"])["dmgnom_fire"]
    if (tot.reindex(chk.index, fill_value=0) - chk).abs().max() > 0.5:
        raise ValueError("the wildfire records do not add up to the damage in the panel")

    rep = repeated_records(fires)
    rep["state_postal"] = rep["state"].map(names)
    drop = rep.groupby(["state_postal", "year"])["repeated"].sum().rename("repeated_nom").reset_index()
    sy = sy.merge(drop, on=["state_postal", "year"], how="left").fillna({"repeated_nom": 0.0})

    wide = repeated_records(fires, same_zone=False)
    wide["state_postal"] = wide["state"].map(names)
    extra = (wide.groupby(["state_postal", "year"])["repeated"].sum().rename("repeated_nom_anyzone")
             .reset_index())
    sy = sy.merge(extra, on=["state_postal", "year"], how="left").fillna({"repeated_nom_anyzone": 0.0})
    factor = sy["year"].map(cpi[2024] / cpi)
    sy["repeated_24_anyzone"] = sy["repeated_nom_anyzone"] * factor
    sy["repeated_24"] = sy["repeated_nom"] * factor
    sy["dmg24_fire_corr"] = (sy["dmgnom_fire"] - sy["repeated_nom"]) * factor
    sy["lndmg_fire_corr"] = np.where(sy["repeated_nom"] > 0, np.log1p(sy["dmg24_fire_corr"] / 1e6),
                                     sy["lndmg_fire"])
    return sy, rep


def build_panel(data, master, market, fires):
    panel = pd.read_csv(data / "insurer_state_year.csv")
    keep = (["id_ins", "identifier", "state_postal", "state_name_uc", "year", "in_sample",
             "insurer_in_year", "p_total", "p_fedflood", "dmgnom_fire", "dmg24_fire"]
            + [f"share_{g}" for g in LINES] + [f"p_{g}" for g in LINES]
            + [c for c in panel.columns if c.startswith("lndmg_")])
    panel = panel[keep].sort_values(["id_ins", "state_postal", "year"]).reset_index(drop=True)
    n0 = len(panel)

    # insurance violations in t-1, t and t+1
    viol = pd.read_csv(data / "violations_insurer_state_year.csv").rename(columns={"state": "state_postal"})
    for h, k in [("l1", -1), ("t0", 0), ("f1", 1)]:
        v = viol.assign(year=viol["penalty_year"] - k)[["id_ins", "state_postal", "year", "n_insviol", "pen_insviol"]]
        v = v.rename(columns={"n_insviol": f"count_{h}", "pen_insviol": f"pen_{h}"})
        panel = panel.merge(v, on=["id_ins", "state_postal", "year"], how="left")
        panel[f"lnpen_{h}"] = np.log1p(panel[f"pen_{h}"])
        panel[f"any_{h}"] = (panel[f"count_{h}"] > 0).astype(float).where(panel[f"count_{h}"].notna())

    # market totals over all insurers, and losses of the linked insurers
    m = master[master["territory"] == 0]
    pw = m["premiums_written"].fillna(0).clip(lower=0)
    mkt = pd.DataFrame({"state_postal": m["state_postal"], "year": m["year"]})
    for g in LINES:
        mkt[f"mkt_{g}"] = pw * m[f"lg_{g}"]
    mkt = mkt.groupby(["state_postal", "year"], as_index=False).sum()

    linked = m[m["id_ins"].notna()]
    pe = linked["premiums_earned"].fillna(0).clip(lower=0)
    li = linked["losses_incurred"].fillna(0)
    loss = pd.DataFrame({"identifier": linked["identifier"], "state_postal": linked["state_postal"],
                         "year": linked["year"], "pe_total": pe * (1 - linked["lg_fedflood"]),
                         "l_fire4": li * linked["lg_fire4"], "l_prop5": li * linked["lg_prop5"]})
    loss = loss.groupby(["identifier", "state_postal", "year"], as_index=False).sum()

    panel = panel.merge(mkt, on=["state_postal", "year"], how="left")
    panel = panel.merge(loss, on=["identifier", "state_postal", "year"], how="left")
    present = panel["insurer_in_year"] == 1
    for c in ["pe_total", "l_fire4", "l_prop5"]:
        panel.loc[present & panel[c].isna(), c] = 0.0

    # market share: the insurer's premiums over all insurers' premiums, same lines, state and year
    ok = present & (panel["year"] <= LAST_MS_YEAR)
    for g in LINES:
        tot = panel[f"mkt_{g}"]
        share = np.where(tot > 0, panel[f"p_{g}"] / tot.where(tot > 0), 0.0)
        panel[f"mshare_{g}"] = np.where(ok, share, np.nan)
    panel["in_sample_ms"] = ((panel["in_sample"] == 1) & (panel["year"] <= LAST_MS_YEAR)).astype(int)

    # losses in the exposed lines over premiums earned, winsorised at 1% and 99%
    for g in ["fire4", "prop5"]:
        pos = panel["pe_total"] > 0
        ratio = pd.Series(np.where(pos, panel[f"l_{g}"] / panel["pe_total"].where(pos), np.nan))
        lo, hi = ratio[panel["in_sample"] == 1].quantile([0.01, 0.99])
        panel[f"lossburden_{g}"] = ratio.clip(lo, hi)

    panel["iy"] = panel["identifier"].astype(str) + "_" + panel["year"].astype(str)
    panel["ist"] = panel["identifier"].astype(str) + "_" + panel["state_postal"]
    panel["sy"] = panel["state_postal"] + "_" + panel["year"].astype(str)

    # violations by offense type, t+1 to t+3
    records = pd.read_csv(data / "violation_records.csv")
    off = offense_outcomes(records)
    panel = panel.merge(off, on=["id_ins", "state_postal", "year"], how="left")
    present = panel["insurer_in_year"] == 1
    for c in off.columns:
        if c.startswith(("count_", "pen_")):
            panel.loc[present & panel[c].isna(), c] = 0.0
    for key in ["ins", "non"]:
        for h in [1, 2, 3]:
            n, p = f"count_{key}_f{h}", f"pen_{key}_f{h}"
            panel[f"lnpen_{key}_f{h}"] = np.log1p(panel[p])
            panel[f"any_{key}_f{h}"] = (panel[n] > 0).astype(float).where(panel[n].notna())
    chk = panel[present & panel["count_f1"].notna() & panel["count_ins_f1"].notna()]
    if (chk["count_f1"] != chk["count_ins_f1"]).any():
        raise ValueError("violation counts by offense type do not match the violations file")

    # damage: change, lag, dummies, and the series with repeated monthly records counted once
    dmg, notes = damage_measures(panel)
    panel = panel.merge(dmg, on=["state_postal", "year"], how="left")
    cpi = master.groupby("year")["CPI"].first()
    corr, rep = corrected_damage(panel, fires, cpi)
    panel = panel.merge(corr[["state_postal", "year", "lndmg_fire_corr"]], on=["state_postal", "year"], how="left")

    # size: ln premiums written by the insurer in all lines in the state, USD million
    with np.errstate(divide="ignore"):
        size = np.log((panel["p_total"] + panel["p_fedflood"].fillna(0)) / 1e6)
    panel["lnsize"] = np.where(panel["in_sample"] == 1, size, np.nan)

    # exposure fixed in the first year (2014, or the insurer-state's first later year), and in t-1
    first = (panel[(panel["in_sample"] == 1) & (panel["year"] >= FIRST_YEAR)]
             .groupby(["id_ins", "state_postal"])["year"].min().rename("base_year").reset_index())
    panel = panel.merge(first, on=["id_ins", "state_postal"], how="left")
    at_base = panel[panel["year"] == panel["base_year"]]
    cols = [f"{e}_{g}" for e in ["share", "mshare"] for g in LINES]
    base = at_base[["id_ins", "state_postal"] + cols].rename(columns={c: f"{c}_base" for c in cols})
    panel = panel.merge(base, on=["id_ins", "state_postal"], how="left")
    panel["after_base"] = (panel["year"] > panel["base_year"]).astype(int)
    lag = panel[["id_ins", "state_postal", "year"] + cols].copy()
    lag["year"] += 1
    panel = panel.merge(lag.rename(columns={c: f"{c}_l1" for c in cols}),
                        on=["id_ins", "state_postal", "year"], how="left")

    # profitability in the first year: loss ratio of the fire lines
    fl = master[(master["territory"] == 0) & (master["lg_fire4"] == 1)]
    by_ins = (fl.assign(pe=fl["premiums_earned"].fillna(0).clip(lower=0), li=fl["losses_incurred"].fillna(0))
              .groupby(["identifier", "state_postal", "year"], as_index=False)[["pe", "li"]].sum())
    mkt0 = by_ins[by_ins["year"] == FIRST_YEAR].groupby("state_postal")[["pe", "li"]].sum()
    state = pd.DataFrame({"lr_state": mkt0["li"] / mkt0["pe"]})
    state["low_state"] = (state["lr_state"] > state["lr_state"].median()).astype(float)
    panel = panel.merge(state.reset_index(), on="state_postal", how="left")
    own = panel[["identifier", "state_postal", "base_year"]].drop_duplicates()
    own = own.merge(by_ins.rename(columns={"year": "base_year"}),
                    on=["identifier", "state_postal", "base_year"], how="left")
    own["lr_is"] = np.where(own["pe"] > 0, own["li"] / own["pe"].where(own["pe"] > 0), np.nan)
    panel = panel.merge(own[["identifier", "state_postal", "lr_is"]].drop_duplicates(["identifier", "state_postal"]),
                        on=["identifier", "state_postal"], how="left")
    cells = panel[(panel["in_sample"] == 1) & (panel["after_base"] == 1)].drop_duplicates(["id_ins", "state_postal"])
    panel["low_is"] = np.where(panel["lr_is"].notna(),
                               (panel["lr_is"] > cells["lr_is"].median()).astype(float), np.nan)

    # regulation: R Street 2024 scores, a fixed state characteristic. A low score means tight
    # regulation. The report covers the 50 states, not DC.
    reg = pd.read_csv(data / "rstreet_scores_2024.csv")
    reg = reg.rename(columns={"t6_combined": "reg_free", "t5_combined": "reg_rate"})
    reg["reg_free_rc"] = reg[["auto", "home", "wc", "medmal", "comm", "desk_drawer", "credit", "territory"]].sum(axis=1)
    splits = []
    for c in ["reg_free", "reg_free_rc", "reg_rate", "home", "comm"]:
        med = reg[c].median()
        reg[f"tight_{c}"] = (reg[c] < med).astype(float)
        splits.append({"measure": c, "median": med, "ties_at_median": int((reg[c] == med).sum()),
                       "n_tight": int((reg[c] < med).sum()),
                       "n_tight_if_ties_included": int((reg[c] <= med).sum())})
    keep = ["state_postal", "reg_free", "reg_free_rc", "reg_rate", "home"] + [c for c in reg if c.startswith("tight_")]
    panel = panel.merge(reg[keep], on="state_postal", how="left")

    # market structure in year t, in t-1 and in the first year; 2014-2023 only
    mk = market.copy()
    mcols = [c for c in mk.columns if c not in ("state_postal", "year")]
    mk.loc[~mk["year"].between(FIRST_YEAR, LAST_MS_YEAR), mcols] = np.nan
    panel = panel.merge(mk.rename(columns={c: f"{c}_t" for c in mcols}), on=["state_postal", "year"], how="left")
    mk_l1 = mk.copy()
    mk_l1["year"] += 1
    panel = panel.merge(mk_l1.rename(columns={c: f"{c}_l1" for c in mcols}), on=["state_postal", "year"], how="left")
    mk0 = mk[mk["year"] == FIRST_YEAR].drop(columns="year")
    panel = panel.merge(mk0.rename(columns={c: f"{c}_first" for c in mcols}), on="state_postal", how="left")

    # captive consumers: few insurers, or a high top-3 / top-5 share. Cut at the median over
    # states; within each year for the t-1 and year-t measures.
    sy_keys = panel.drop_duplicates(["state_postal", "year"])[["state_postal", "year"]]
    captive = {}
    for when in ["first", "l1", "t"]:
        for stem, col, op in [("fewins", f"nins_fire4_{when}", "le"), ("hitop3", f"top3_fire4_{when}", "gt"),
                              ("hitop5", f"top5_fire4_{when}", "gt")]:
            vals = sy_keys.merge(panel[["state_postal", "year", col]].drop_duplicates(["state_postal", "year"]),
                                 on=["state_postal", "year"])
            if when == "first":
                cut = pd.Series(vals[col].median(), index=panel.index)
            else:
                cut = panel["year"].map(vals.groupby("year")[col].median())
            flag = (panel[col] <= cut) if op == "le" else (panel[col] > cut)
            captive[f"{stem}_fire4_{when}"] = flag.astype(float).where(panel[col].notna())
    panel = pd.concat([panel, pd.DataFrame(captive, index=panel.index)], axis=1)

    if len(panel) != n0:
        raise ValueError(f"a merge changed the number of rows: {n0} -> {len(panel)}")
    return panel, notes, pd.DataFrame(splits), corr, rep


# ----------------------------------------------------------------------------- regressions

def feols(df, y, rhs, fes, cluster="identifier"):
    fml = f"{y} ~ {' + '.join(rhs)}" + (f" | {' + '.join(fes)}" if fes else "")
    return pf.feols(fml, data=df, vcov={"CRV1": cluster}, fixef_rm="singleton",
                    ssc=pf.ssc(k_fixef="nonnested"))


def ols(df, y, rhs, fes, cluster="identifier", optional=(), combo=None):
    """OLS with fixed effects. Regressors in `optional` may be absorbed by the fixed effects;
    any other regressor that drops out is an error. Returns the estimates by regressor."""
    m = feols(df, y, rhs, fes, cluster)
    names = list(m._coefnames)
    missing = [v for v in rhs if v not in names and v not in optional]
    if missing:
        raise ValueError(f"collinear with the fixed effects: {missing}")
    used = m._data
    info = {"N": m._N, "clusters": used[cluster.split("+")[0]].nunique(),
            "mean_y": float(used[y].mean()), "sd_x": float(used[rhs[0]].std())}
    b, se, p = m.coef(), m.se(), m.pvalue()
    est = {v: (float(b[v]), float(se[v]), float(p[v])) for v in names}
    if combo:
        i, j = names.index(combo[0]), names.index(combo[1])
        v = m._vcov
        s = float(np.sqrt(v[i, i] + v[j, j] + 2 * v[i, j]))
        e = b[combo[0]] + b[combo[1]]
        est["x_sum"] = (float(e), s, float(2 * stats.t.sf(abs(e / s), m._df_t)))
    return est, info


def frame(panel, kind, design, horizon=1, sample="all"):
    """Estimation sample. Outcome years after 2024 are not fully observed and are dropped."""
    df = panel[panel["in_sample_ms" if kind == "mshare" else "in_sample"] == 1]
    if design in ("base", "lag"):
        df = df[df["after_base"] == 1]
    if sample == "notxwa":
        df = df[~df["state_postal"].isin(["TX", "WA"])]
    elif sample == "from2014":
        df = df[df["year"] >= FIRST_YEAR]
    return df[df["year"] + horizon <= LAST_VIOL_YEAR]


def run(panel, s):
    """One specification. Returns a row per term, the lower-order terms included; a term the
    fixed effects absorb is returned with absorbed = 1."""
    fes = FE[s["fe"]]
    kind, group, design = s["expo"], s["group"], s["design"]
    e = f"{kind}_{group}" + {"current": "", "base": "_base", "lag": "_l1"}[design]
    if s["damage"] == "levlag":
        d, dl = f"lndmg_{s['hazard']}", f"lndmg_l1_{s['hazard']}"
    else:
        d, dl = f"{s['damage']}_{s['hazard']}", None
    if s["dv"] == "lossburden":
        y = f"lossburden_{group}"
    elif s["outcome"] == "all":
        y = f"{s['dv']}_f{s['horizon']}"
    else:
        y = f"{s['dv']}_{s['outcome']}_f{s['horizon']}"

    d_absorbed = "sy" in fes                      # state-year variables
    e_absorbed = design == "base" and "ist" in fes
    ctrl = [c.format(group=group) for c in CONTROLS[s["controls"]]]
    ctrl_absorbed = [c for c in ctrl if c.startswith("hhi_") and d_absorbed]
    ctrl = [c for c in ctrl if c not in ctrl_absorbed]

    df = frame(panel, kind, design, s["horizon"], s["sample"]).copy()
    mod = s.get("moderator")
    cluster = CLUSTER[s["cluster"]]
    need = [y, e, d] + ([dl] if dl else []) + ([mod] if mod else []) + ctrl
    df = df[df[need].notna().all(axis=1)]
    df["x"] = df[e] * df[d]

    terms = {"x": "x", "expo": e, "dmg": d}
    if dl:
        df["x_l"] = df[e] * df[dl]
        rhs = ["x", "x_l"] + ([] if e_absorbed else [e]) + ([] if d_absorbed else [d, dl]) + ctrl
        est, info = ols(df, y, rhs, fes, cluster, combo=("x", "x_l"))
        terms.update({"x_l": "x_l", "dmg_l1": dl, "x_sum": "x_sum"})
    elif mod:
        df["x_m"] = df["x"] * df[mod]
        df["d_m"] = df[d] * df[mod]
        df["e_m"] = df[e] * df[mod]
        dm_absorbed = d_absorbed and MODERATORS[mod] == "state"
        lower = ["e_m", mod]
        rhs = (["x", "x_m"] + ([] if dm_absorbed else ["d_m"]) + lower + ([] if e_absorbed else [e])
               + ([] if d_absorbed else [d]) + ctrl)
        est, info = ols(df, y, rhs, fes, cluster, optional=lower, combo=("x", "x_m"))
        terms = {"x": "x", "x_m": "x_m", "d_m": "d_m", "e_m": "e_m", "mod": mod, "expo": e, "dmg": d,
                 "x_sum": "x_sum"}
    else:
        rhs = ["x"] + ([] if e_absorbed else [e]) + ([] if d_absorbed else [d]) + ctrl
        est, info = ols(df, y, rhs, fes, cluster)
    terms.update({c: c for c in ctrl + ctrl_absorbed})

    rows = []
    for term, col in terms.items():
        if col in est:
            b, se, p = est[col]
            rows.append({**s, "term": term, "b": b, "se": se, "p": p, **info, "absorbed": 0})
        else:
            rows.append({**s, "term": term, "absorbed": 1})
    return rows


def specifications():
    specs = []

    def add(table, pan, **kw):
        base = dict(table=table, panel=pan, expo="mshare", group="fire4", hazard="fire", damage="lndmg",
                    dv="count", horizon=1, outcome="all", fe="F7", design="current", controls="none",
                    cluster="insurer", moderator=None, sample="all")
        specs.append({**base, **kw})

    # Table 2: baseline
    for dv in DVS:
        for fe in MAIN_FE:
            add("2", "A", dv=dv, fe=fe)
            add("2", "B", dv=dv, fe=fe, expo="share")
            add("2", "C", dv=dv, fe=fe, sample="from2014")
            add("2", "C", dv=dv, fe=fe, sample="from2014", controls="size_hhi")
            add("2", "D", dv=dv, fe=fe, damage="dlndmg")

    # Table 3: other line groups
    for group, hazard in PAIRS:
        for dv in DVS:
            for fe in MAIN_FE:
                add("3", "A", group=group, hazard=hazard, dv=dv, fe=fe)

    # Table 4: exposure fixed in the first year or in t-1; damage dummies; level and lag
    for dv in DVS:
        for fe in MOD_FE:
            add("4", "A", dv=dv, fe=fe, design="base")
            add("4", "B", dv=dv, fe=fe, design="lag")
            add("4", "C", dv=dv, fe=fe, design="base", damage="dlndmg")
            add("4", "D", dv=dv, fe=fe, design="lag", damage="dlndmg")
        for measure in DUMMY_MEASURES:
            for fe in MAIN_FE:
                add("4", "E", dv=dv, fe=fe, damage=measure)
            add("4", "F", dv=dv, fe="F7", damage=measure, design="base")
        for fe in MAIN_FE:
            add("4", "G", dv=dv, fe=fe, damage="levlag")

    # Table 5: violations in t+1 to t+3, and non-insurance violations
    for dv in DVS:
        for fe in MAIN_FE:
            for h in [1, 2, 3]:
                add("5", "A", dv=dv, fe=fe, horizon=h, outcome="ins")
                add("5", "B", dv=dv, fe=fe, horizon=h, outcome="non")

    # Tables 6-8: moderators, first-year exposure, size control
    groups = {"6": ["low_state", "low_is"],
              "7": ["tight_reg_free", "tight_reg_free_rc", "tight_reg_rate", "tight_home"],
              "8": ["fewins_fire4_first", "hitop3_fire4_first", "hitop5_fire4_first",
                    "fewins_fire4_l1", "hitop3_fire4_l1", "hitop5_fire4_l1"]}
    for table, mods in groups.items():
        for pan, mod in zip("ABCDEF", mods):
            for dv in DVS:
                for fe in MOD_FE:
                    add(table, pan, dv=dv, fe=fe, design="base", controls="size", moderator=mod)

    # Appendix 2: clustering
    for cl in CLUSTER:
        for dv in DVS:
            for fe in MAIN_FE:
                add("A2", "A", dv=dv, fe=fe, cluster=cl)

    # Appendix 3: exposure in t with insurer x year effects; losses in the exposed lines;
    # market structure in the damage year
    for dv in DVS:
        for fe in ["F9", "F10"]:
            add("A3", "A", dv=dv, fe=fe)
    for expo in ["mshare", "share"]:
        for group, hazard in LOSS_PAIRS:
            for fe in MAIN_FE:
                add("A3", "B", expo=expo, group=group, hazard=hazard, dv="lossburden", horizon=0, fe=fe)
    for pan, mod in zip("CD", ["fewins_fire4_t", "hitop3_fire4_t"]):
        for dv in DVS:
            for fe in MOD_FE:
                add("A3", pan, dv=dv, fe=fe, design="base", controls="size", moderator=mod)

    # Appendix 4: placebo hazards
    for hazard in PLACEBO_HAZARDS:
        for dv in DVS:
            for fe in MAIN_FE:
                add("A4", "A", hazard=hazard, dv=dv, fe=fe)

    # Appendix 5: without Texas and Washington
    for dv in DVS:
        for fe in MAIN_FE:
            add("A5", "A", dv=dv, fe=fe, sample="notxwa")
            add("A5", "B", dv=dv, fe=fe, sample="notxwa", damage="dlndmg")

    # Appendix 6: wildfire damage with repeated monthly records counted once
    for dv in DVS:
        for fe in MAIN_FE:
            add("A6", "A", dv=dv, fe=fe, damage="lndmg", hazard="fire_corr")
    return specs


# ----------------------------------------------------------------------------- variables

HAZARD_NAME = {"all": "all hazards", "flood": "floods", "tropical": "tropical cyclones",
               "convective": "convective storms (hail, tornado, thunderstorm wind)",
               "wind": "other wind", "winter": "winter storms", "drought_heat": "drought and heat"}
GROUP_NAME = {"fireonly": "fire only", "firehome": "fire and homeowners",
              "firebroad": "the fire lines plus allied lines, inland marine, and commercial and private passenger auto physical damage",
              "prop5": "the fire lines plus allied lines", "privflood": "private flood",
              "crop": "crop (multiple peril and hail)"}
VT, CIQ, NOAA, RS = ("Violation Tracker", "S&P Capital IQ", "NOAA Storm Events", "R Street Policy Study No. 314")


def definitions(notes, reg_raw):
    """Label, definition and source of every variable (Appendix 1)."""
    fire = notes.set_index("hazard").loc["fire"]
    zero, zero_ch = f"{fire['share_zero_level']:.0%}", f"{fire['share_zero_change']:.0%}"
    p75 = f"USD {round(fire['p75_usd_million'] * 1e6, -3):,.0f}"
    p90 = f"USD {fire['p90_usd_million']:.1f} million"
    lo = reg_raw.loc[reg_raw["t6_combined"].idxmin()]
    hi = reg_raw.loc[reg_raw["t6_combined"].idxmax()]

    d = {
        "count_f1": ("Insurance violations in t+1", "Number of insurance-violation penalties imposed on the insurer by an agency of the state in year t+1 (penalty year).", VT),
        "lnpen_f1": ("Ln penalties in t+1", "ln(1 + total penalty imposed in t+1, constant USD).", VT),
        "any_f1": ("Any violation in t+1", "1 if the insurer has at least one insurance violation in the state in t+1.", VT),
    }
    for h in (1, 2, 3):
        for key, what in [("ins", "insurance violations"), ("non", "non-insurance violations")]:
            extra = ("" if key == "ins" else " Offense categories other than insurance violations, imposed by an agency of one state. Federal cases, mostly railroad and workplace safety, have no state and cannot enter the panel.")
            d[f"count_{key}_f{h}"] = (f"Number of {what}, t+{h}", f"Number of {what} penalties in year t+{h}, from the violation records by offense type.{extra} Observed only where t+{h} <= 2024.", VT)
            d[f"lnpen_{key}_f{h}"] = (f"Ln penalties, {what}, t+{h}", f"ln(1 + total penalty for {what} in t+{h}).", VT)
            d[f"any_{key}_f{h}"] = (f"Any {what[:-1]}, t+{h}", f"1 if at least one {what[:-1]} in t+{h}.", VT)
    d.update({
        "lossburden_fire4": ("Losses in the fire lines, year t", "Losses incurred in the fire lines divided by premiums earned in all lines (Federal flood excluded), insurer-state-year, winsorised at 1% and 99%.", CIQ),
        "lossburden_prop5": ("Losses in the property lines, year t", "As above for the fire lines plus allied lines.", CIQ),
        "mshare_fire4": ("Market share, fire lines", "Premiums the insurer writes in the fire lines (fire, homeowners, farmowners, commercial multiple peril non-liability) in the state in year t, divided by the premiums all insurers write in those lines in that state and year.", CIQ),
        "share_fire4": ("Portfolio share, fire lines", "Premiums the insurer writes in the fire lines in the state in year t, divided by its premiums in all lines in that state and year.", CIQ),
        "share_prop5": ("Portfolio share, property lines", "As above for the fire lines plus allied lines.", CIQ),
        "mshare_fire4_base": ("Market share, first year", "Market share in the first year of the sample period for that insurer-state: 2014, or the first later year with premiums.", CIQ),
        "mshare_fire4_l1": ("Market share in t-1", "Market share in the preceding year.", CIQ),
    })
    for g, name in GROUP_NAME.items():
        d[f"mshare_{g}"] = (f"Market share, {name}", f"Market share as above, for {name}.", CIQ)
    d["lndmg_fire"] = ("Ln wildfire damage", "ln(1 + property and crop damage from wildfire in the state in year t, USD million of 2024).", NOAA)
    for h, name in HAZARD_NAME.items():
        d[f"lndmg_{h}"] = (f"Ln damage, {name}", f"ln(1 + property and crop damage from {name} in the state in year t, USD million of 2024).", NOAA)
    d.update({
        "dlndmg_fire": ("Change in ln wildfire damage", f"ln(1 + D in t) - ln(1 + D in t-1). Defined at zero damage; missing only in 2013, the first year; zero in {zero_ch} of state-years (no wildfire damage in either year).", NOAA),
        "lndmg_l1_fire": ("Ln wildfire damage in t-1", "ln(1 + wildfire damage in t-1).", NOAA),
        "dmgany_fire": ("Any wildfire damage", f"1 if wildfire damage in the state in year t is above zero. Damage is zero in {zero} of state-years, so the median is zero and this is also the above-median dummy.", NOAA),
        "dmgp75_fire": ("Wildfire damage in the top quartile", f"1 if wildfire damage is above the 75th percentile of the state-year distribution, zeros included (about {p75}).", NOAA),
        "d_dmgp75_fire": ("Change in the top-quartile dummy", "dmgp75 in year t minus dmgp75 in t-1: +1 when a state crosses into the top quartile, -1 when it drops out, 0 otherwise.", NOAA),
        "dmgp90_fire": ("Wildfire damage in the top decile", f"1 if wildfire damage is above the 90th percentile of the state-year distribution, zeros included (about {p90}).", NOAA),
        "dmg10m_fire": ("Wildfire damage above USD 10 million", "1 if wildfire damage in the state in year t is above USD 10 million of 2024.", NOAA),
        "dmg100m_fire": ("Wildfire damage above USD 100 million", "1 if wildfire damage in the state in year t is above USD 100 million of 2024.", NOAA),
        "lndmg_fire_corr": ("Ln wildfire damage, repeated records removed", "As ln wildfire damage, but when a fire's record for one month repeats the damage of its record for the previous month in the same forecast zone, the repeated amount is counted once.", NOAA),
        "lr_state": ("Loss ratio of the state market, first year", "Losses incurred divided by premiums earned in the fire lines, all insurers in the state, in 2014.", CIQ),
        "low_state": ("Low profitability of the state market", "1 if lr_state is above the median of the 51 states.", CIQ),
        "lr_is": ("Loss ratio of the insurer in the state, first year", "Losses incurred divided by premiums earned in the fire lines, the insurer in that state, in its first year.", CIQ),
        "low_is": ("Low profitability of the insurer-state", "1 if lr_is is above the median of the insurer-states in the regressions.", CIQ),
        "reg_free": ("Underwriting freedom, 2024", f"Combined underwriting-freedom score of the state as printed in Table 6 of the R Street 2024 Insurance Regulation Report Card. Higher means freer. Range {lo['t6_combined']} ({lo['state_postal']}) to {hi['t6_combined']} ({hi['state_postal']}). The report covers the 50 states, not DC.", RS),
        "reg_free_rc": ("Underwriting freedom, rebuilt from components", "The five line scores of Table 5 plus desk drawer, credit scoring and territory from Table 6. It removes the five states where the report's own arithmetic fails (Nebraska's Table 5 total; the Table 6 totals of Colorado, Nevada and Washington; Louisiana's rate-regulation entry in Table 6, which differs from its Table 5 total).", RS),
        "reg_rate": ("Rate regulation, 2024", "Combined rate-regulation score over five lines, Table 5 of the same report. Higher means freer.", RS),
        "home": ("Rate regulation, homeowners", "The homeowners column of Table 5.", RS),
    })
    for c, name in [("reg_free", "underwriting freedom (printed)"), ("reg_free_rc", "underwriting freedom (rebuilt from components)"),
                    ("reg_rate", "rate regulation"), ("home", "homeowners rate regulation")]:
        d[f"tight_{c}"] = (f"Tight regulation: {name}", f"1 if the {name} score is strictly below the median of the 50 states; states exactly at the median count as free.", RS)
    for when, wname, how in [("first", "2014", "the median across the 51 states in 2014"),
                             ("l1", "t-1", "the median across states in that same year"),
                             ("t", "year t", "the median across states in that same year")]:
        d[f"nins_fire4_{when}"] = (f"Insurers in the fire lines, {wname}", f"Number of insurer groups writing positive premiums in the fire lines in the state, {wname}. Market structure exists for 2014-2023 only.", CIQ)
        d[f"top3_fire4_{when}"] = (f"Top-3 share, fire lines, {wname}", f"Share of the state's fire-line premiums written by the three largest insurer groups, {wname}.", CIQ)
        d[f"top5_fire4_{when}"] = (f"Top-5 share, fire lines, {wname}", f"Share written by the five largest insurer groups, {wname}.", CIQ)
        d[f"fewins_fire4_{when}"] = (f"Few insurers, {wname}", f"1 if the number of insurers is at or below {how}." + (" Measured in the damage year itself; reported in Appendix 3 only." if when == "t" else ""), CIQ)
        d[f"hitop3_fire4_{when}"] = (f"High top-3 share, {wname}", f"1 if the top-3 share is above {how}.", CIQ)
        d[f"hitop5_fire4_{when}"] = (f"High top-5 share, {wname}", f"1 if the top-5 share is above {how}.", CIQ)
    d.update({
        "lnsize": ("Ln insurer size in the state", "ln(premiums written by the insurer in all lines in the state in year t, Federal flood included, USD million).", CIQ),
        "hhi_fire4_t": ("HHI, fire lines, year t", "Herfindahl-Hirschman index of fire-line premium shares in the state in year t, 0 to 10,000. A state-year variable, so state x year effects absorb it.", CIQ),
    })
    return d


ORDER = (["count_f1", "lnpen_f1", "any_f1"]
         + [f"{d}_{k}_f{h}" for k in ("ins", "non") for h in (1, 2, 3) for d in ("count", "lnpen", "any")]
         + ["lossburden_fire4", "lossburden_prop5",
            "mshare_fire4", "share_fire4", "share_prop5", "mshare_fire4_base", "mshare_fire4_l1"]
         + [f"mshare_{g}" for g in GROUP_NAME]
         + ["lndmg_fire"] + [f"lndmg_{h}" for h in HAZARD_NAME]
         + ["dlndmg_fire", "lndmg_l1_fire", "dmgany_fire", "dmgp75_fire", "d_dmgp75_fire",
            "dmgp90_fire", "dmg10m_fire", "dmg100m_fire", "lndmg_fire_corr",
            "lr_state", "low_state", "lr_is", "low_is",
            "reg_free", "reg_free_rc", "reg_rate", "home",
            "tight_reg_free", "tight_reg_free_rc", "tight_reg_rate", "tight_home"]
         + [f"{s}_fire4_{w}" for w in ("first", "l1") for s in ("nins", "top3", "top5", "fewins", "hitop3", "hitop5")]
         + [f"{s}_fire4_t" for s in ("nins", "top3", "fewins", "hitop3")]
         + ["lnsize", "hhi_fire4_t"])

MODERATOR_STEMS = ("low_", "lr_", "tight_", "reg_", "home", "nins_", "top3_", "top5_", "fewins_", "hitop3_", "hitop5_")


def sample_for(v):
    """(design, horizon) of the sample a variable enters."""
    if v.endswith("_f2"):
        return "current", 2
    if v.endswith("_f3"):
        return "current", 3
    if v.startswith("lossburden_"):
        return "current", 0
    if v == "hhi_fire4_t":
        return "current", 1
    if v.endswith(("_base", "_l1")) and v.startswith(("mshare_", "share_")):
        return "base", 1
    if v.startswith(MODERATOR_STEMS):
        return "base", 1
    return "current", 1


def summary(panel, labels):
    rows = []
    for kind in ["mshare", "share"]:
        for v in ORDER:
            if v not in panel.columns:
                continue
            if (kind == "mshare" and v.startswith("share_")) or (kind == "share" and v.startswith("mshare")):
                continue
            design, horizon = sample_for(v)
            x = frame(panel, kind, design, horizon)[v].dropna()
            if not len(x):
                continue
            q = x.quantile([0.25, 0.5, 0.75])
            rows.append({"sample": kind, "variable": v, "label": labels.get(v, ""), "N": len(x),
                         "mean": x.mean(), "sd": x.std(), "p25": q[0.25], "median": q[0.5], "p75": q[0.75]})
    return pd.DataFrame(rows)


def check_rstreet(reg):
    """The report's own totals do not add up for five states; stop if the file shows others."""
    lines = reg[["auto", "home", "wc", "medmal", "comm"]].sum(axis=1)
    rate = reg[["rate_reg", "desk_drawer", "credit", "territory"]].sum(axis=1)
    found = (set(reg.loc[reg["t5_combined"] != lines, "state_postal"])
             | set(reg.loc[reg["t6_combined"] != rate, "state_postal"])
             | set(reg.loc[reg["rate_reg"] != reg["t5_combined"], "state_postal"]))
    if found != {"NE", "CO", "NV", "WA", "LA"} or len(reg) != 50:
        raise ValueError(f"unexpected R Street scores: {sorted(found)}")


# ----------------------------------------------------------------------------- power and checks

def mde(se, clusters):
    df = clusters - 1
    return (stats.t.ppf(0.975, df) + stats.t.ppf(0.80, df)) * se


def power(res, panel):
    """Minimum detectable effects (80% power, 5% two-sided) of the triple interactions against
    the baseline of the same design, and the insurer-state-years that identify them."""
    est = res[res["absorbed"] == 0]
    base = {(r["dv"], r["fe"]): r["b"] for _, r in
            est[(est["table"] == "4") & (est["panel"] == "A") & (est["term"] == "x")].iterrows()}
    mods = est[est["table"].isin(["6", "7", "8", "A3"]) & (est["term"] == "x_m")].copy()
    mods["mde"] = mde(mods["se"], mods["clusters"])
    mods["baseline_same_design"] = [base[(r["dv"], r["fe"])] for _, r in mods.iterrows()]
    mods["mde_over_baseline"] = mods["mde"] / mods["baseline_same_design"].abs()
    pw = mods[["table", "panel", "moderator", "dv", "fe", "b", "se", "p", "mde",
               "baseline_same_design", "mde_over_baseline", "N"]]

    t4 = est[(est["table"] == "4") & est["panel"].isin(["E", "F"]) & (est["term"] == "x")]
    ref = {(r["panel"], r["dv"], r["fe"]): r for _, r in t4[t4["damage"] == "lndmg"].iterrows()}
    rows = []
    for _, r in t4[t4["damage"] == "d_dmgp75"].iterrows():
        b0 = ref[(r["panel"], r["dv"], r["fe"])]
        m = mde(r["se"], r["clusters"])
        rows.append({"panel": r["panel"], "dv": r["dv"], "fe": r["fe"], "b": r["b"], "p": r["p"],
                     "mde_per_sd": m * r["sd_x"], "lndmg_effect_per_sd": abs(b0["b"]) * b0["sd_x"],
                     "ratio_per_sd": m * r["sd_x"] / (abs(b0["b"]) * b0["sd_x"])})
    dch = pd.DataFrame(rows)

    d = frame(panel, "mshare", "base", 1)
    hit = d[(d["any_f1"] > 0) & (d["lndmg_fire"] > 0)]
    rows = []
    for table, mod in mods[["table", "moderator"]].drop_duplicates().itertuples(index=False):
        k = hit[hit[mod].notna()]
        a0, a1 = int((k[mod] == 0).sum()), int((k[mod] == 1).sum())
        rows.append({"table": table, "moderator": mod, "sample_rows": len(d),
                     "with_violation": int((d["any_f1"] > 0).sum()), "identifying_cells": len(k),
                     "arm_0": a0, "arm_1": a1, "smaller_arm": min(a0, a1)})
    return pw, dch, pd.DataFrame(rows)


def market_exit(market, panel):
    """Change in the number of fire-line insurers and in the top-3 share from t to t+1, after
    years with and without damage, 2014-2023."""
    mk = market[market["year"].between(FIRST_YEAR, LAST_MS_YEAR)].sort_values(["state_postal", "year"])
    raw = panel.drop_duplicates(["state_postal", "year"])[["state_postal", "year", "lndmg_fire", "lndmg_all"]]
    rows = []
    for group, hazard in [("fire4", "fire"), ("prop5", "all")]:
        d = mk[["state_postal", "year", f"nins_{group}", f"top3_{group}"]].copy()
        nxt = d.copy()
        nxt["year"] -= 1
        d = d.merge(nxt, on=["state_postal", "year"], suffixes=("", "_next"))
        d["d_nins"] = d[f"nins_{group}_next"] - d[f"nins_{group}"]
        d["d_top3"] = 100 * (d[f"top3_{group}_next"] - d[f"top3_{group}"])
        d = d.merge(raw, on=["state_postal", "year"])
        dmg = f"lndmg_{hazard}"
        med = d[dmg].median()
        d["high"] = (d[dmg] > med).astype(float)
        split = "any damage vs none" if med == 0 else "above vs at or below the median"
        for outcome in ["d_nins", "d_top3"]:
            hi, lo = d.loc[d["high"] == 1, outcome].mean(), d.loc[d["high"] == 0, outcome].mean()
            m1 = pf.feols(f"{outcome} ~ high | year", data=d, vcov={"CRV1": "state_postal"})
            m2 = pf.feols(f"{outcome} ~ {dmg} | year", data=d, vcov={"CRV1": "state_postal"})
            rows.append({"lines": group, "hazard": hazard, "outcome": outcome, "split": split,
                         "changes_ending": f"{int(d['year'].min()) + 1}-{int(d['year'].max()) + 1}",
                         "n_state_years": len(d), "mean_after_high": hi, "mean_after_low": lo,
                         "raw_difference": hi - lo, "within_year_difference": float(m1.coef()["high"]),
                         "within_year_se": float(m1.se()["high"]), "within_year_p": float(m1.pvalue()["high"]),
                         "per_ln_damage": float(m2.coef()[dmg]), "per_ln_damage_p": float(m2.pvalue()[dmg])})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- Figure 1

EXPO, DAMAGE = "mshare_fire4", "lndmg_fire"
SCHEMES = ["between firms", "across time", "combined", "across states"]
FIG1_ROWS = ["across states", "between firms", "across time", "combined"]
SCHEME_LABEL = {"across states": "damage across states", "between firms": "exposure between firms",
                "across time": "damage across time", "combined": "between firms and across time"}
RED, BLUE = "#C2185B", "#4A90D9"


def fit_t(df, xcol, dv="count_f1"):
    m = feols(df, dv, [xcol, EXPO], FE["F7"])
    b, se = float(m.coef()[xcol]), float(m.se()[xcol])
    return b, b / se


def permute(df, scheme, rng):
    expo = df[EXPO].to_numpy()
    dmg = df[DAMAGE].to_numpy()
    if scheme in ("between firms", "combined"):
        expo = expo.copy()
        for _, idx in df.groupby("sy", sort=False).indices.items():
            expo[idx] = rng.permutation(expo[idx])
    if scheme in ("across time", "combined", "across states"):
        sy = df[["state_postal", "year", DAMAGE]].drop_duplicates(["state_postal", "year"]).copy()
        by = "year" if scheme == "across states" else "state_postal"
        sy["perm"] = sy.groupby(by)[DAMAGE].transform(lambda s: rng.permutation(s.to_numpy()))
        key = {(s, y): v for s, y, v in zip(sy["state_postal"], sy["year"], sy["perm"])}
        dmg = np.array([key[(s, y)] for s, y in zip(df["state_postal"], df["year"])])
    out = df.copy()
    out["xp"] = expo * dmg
    return out


def figure1(panel, out, draws):
    df = panel[panel["in_sample_ms"] == 1].copy()
    df = df[df[["count_f1", EXPO, DAMAGE]].notna().all(axis=1)]
    df["xp"] = df[EXPO] * df[DAMAGE]
    b0, t0 = fit_t(df, "xp")
    rows = []
    for scheme in SCHEMES:
        bs, ts = [], []
        for seed in range(draws):
            b, t = fit_t(permute(df, scheme, np.random.default_rng(seed)), "xp")
            bs.append(b)
            ts.append(t)
        bs, ts = np.array(bs), np.array(ts)
        rows.append({"scheme": scheme, "draws": draws, "b_actual": b0, "t_actual": t0,
                     "p_two_sided": float((1 + np.sum(np.abs(ts) >= abs(t0))) / (draws + 1)),
                     "t_q95": float(np.quantile(np.abs(ts), 0.95)),
                     "b_mean": float(bs.mean()), "b_sd": float(bs.std(ddof=1)),
                     "t_mean": float(ts.mean()), "t_sd": float(ts.std(ddof=1))})
        np.savez(out / f"figure1_draws_{scheme.replace(' ', '_')}.npz", b=bs, t=ts)
        print(f"  Figure 1, {scheme}: p = {rows[-1]['p_two_sided']:.3f}")
    return pd.DataFrame(rows)


def draw_figure1(out, res):
    b0, t0 = res["b_actual"].iloc[0], res["t_actual"].iloc[0]
    draws = int(res["draws"].iloc[0])
    fig, axes = plt.subplots(len(FIG1_ROWS), 2, figsize=(11, 3.1 * len(FIG1_ROWS)))
    for i, scheme in enumerate(FIG1_ROWS):
        d = np.load(out / f"figure1_draws_{scheme.replace(' ', '_')}.npz")
        for j, (vals, actual, name) in enumerate([(d["b"], b0, "Estimator"), (d["t"], t0, "T Value")]):
            ax = axes[i, j]
            lo, hi = vals.min(), vals.max()
            grid = np.linspace(min(lo, actual) - 0.05 * (hi - lo), max(hi, actual) + 0.05 * (hi - lo), 400)
            kde = stats.gaussian_kde(vals)
            ax.plot(grid, stats.norm.pdf(grid, vals.mean(), vals.std(ddof=1)), color=BLUE, lw=1.2, zorder=1)
            ax.scatter(vals, kde(vals), s=9, color=RED, zorder=2)
            ax.axvline(0, color="0.35", lw=0.9)
            ax.axvline(actual, color="0.2", lw=1.1, ls="--")
            ax.set_xlabel(name)
            ax.set_ylabel("Density")
            ax.set_title(f"Figure 1{'abcd'[i]}.{'ab'[j]}  {SCHEME_LABEL[scheme]}", fontsize=10)
            ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle(f"Placebo tests, {draws:,} replications (insurer x state and state x year fixed effects)\n"
                 "dashed line: the actual estimate", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out / "figure1_placebo.png", dpi=200)
    plt.close(fig)


def placebo_cells(panel, draws):
    """Damage reshuffled across states within each year, for the three outcomes under insurer +
    state + year and under insurer x state + state x year effects."""
    d = panel[panel["in_sample_ms"] == 1].copy()
    sy = d[["state_postal", "year", DAMAGE]].drop_duplicates(["state_postal", "year"])

    def t_of(df, dmg, y, fe):
        df = df[df[y].notna()].copy()
        df["x"] = df[EXPO] * df[dmg]
        rhs = ["x", EXPO] + ([] if "sy" in FE[fe] else [dmg])
        m = feols(df, y, rhs, FE[fe])
        return float(m.coef()["x"] / m.se()["x"])

    rows = []
    for y in ["any_f1", "count_f1", "lnpen_f1"]:
        for fe in ["F3", "F7"]:
            t0 = t_of(d, DAMAGE, y, fe)
            ts = []
            for seed in range(draws):
                rng = np.random.default_rng(seed)
                sh = sy.copy()
                sh["perm"] = sh.groupby("year")[DAMAGE].transform(lambda s: rng.permutation(s.values))
                dd = d.merge(sh[["state_postal", "year", "perm"]], on=["state_postal", "year"], how="left")
                ts.append(t_of(dd, "perm", y, fe))
            ts = np.abs(np.array(ts))
            r = int(np.sum(ts >= abs(t0)))
            rows.append({"y": y, "fe": fe, "t": t0, "draws": draws, "p": r / draws,
                         "p_rb": (1 + r) / (draws + 1), "t_95": float(np.quantile(ts, 0.95))})
            print(f"  placebo across states, {y} {fe}: p = {rows[-1]['p_rb']:.3f}")
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- Figure 2

FIG_DVS = ["count_f1", "lnpen_f1", "any_f1"]
DV_LABEL = {"count_f1": "Number of violations", "lnpen_f1": "Ln penalties", "any_f1": "Any violation"}


def tag(k):
    return "x_t" if k == 0 else (f"x_lag{-k}" if k < 0 else f"x_lead{k}")


def dynamic(panel, dv, window=3, design="current", fe="F7", damage=DAMAGE, controls=(), sample=None):
    """All of Exposure x damage(t+k), |k| <= window, in one regression; the outcome is in t+1.
    The damage terms enter on their own unless state x year effects absorb them."""
    sy = panel.drop_duplicates(["state_postal", "year"])[["state_postal", "year", damage]]
    sy = sy.sort_values(["state_postal", "year"])
    lead = sy[["state_postal", "year"]].copy()
    for k in range(-window, window + 1):
        s = sy.copy()
        s["year"] = s["year"] - k
        lead = lead.merge(s.rename(columns={damage: f"dmg_{tag(k)}"}), on=["state_postal", "year"], how="left")
    expo = EXPO if design == "current" else f"{EXPO}_base"
    df = panel[panel["in_sample_ms"] == 1].merge(lead, on=["state_postal", "year"], how="left")
    if design == "base":
        df = df[df["after_base"] == 1]
    if sample == "notxwa":
        df = df[~df["state_postal"].isin(["TX", "WA"])]
    fes = FE[fe]
    ks = list(range(-window, window + 1))
    xs = [tag(k) for k in ks]
    dmg = [f"dmg_{tag(k)}" for k in ks]
    ctrl = [c for c in controls if not (c == "hhi_fire4_t" and "sy" in fes)]
    need = [dv, expo] + dmg + [c for c in ctrl if c != "x_hhi"] + (["hhi_fire4_t"] if "x_hhi" in ctrl else [])
    df = df[df[need].notna().all(axis=1)].copy()
    for k in ks:
        df[tag(k)] = df[expo] * df[f"dmg_{tag(k)}"]
    if "x_hhi" in ctrl:
        df["x_hhi"] = df[expo] * df["hhi_fire4_t"]
    X = df[xs].to_numpy()
    cond, rank = float(np.linalg.cond(X)), int(np.linalg.matrix_rank(X))

    expo_in = not (design == "base" and "ist" in fes)
    rhs = xs + ([expo] if expo_in else []) + ([] if "sy" in fes else dmg) + ctrl
    m = feols(df, dv, rhs, fes)
    b, se, p = m.coef(), m.se(), m.pvalue()
    crit = stats.t.ppf(0.975, m._df_t)
    common = {"window": window, "design": design, "dv": dv, "N": m._N,
              "years": f"{int(df['year'].min())}-{int(df['year'].max())}"}
    rows = []
    for k in ks:
        t = tag(k)
        rows.append({**common, "k": k, "term": t, "b": float(b[t]), "se": float(se[t]), "p": float(p[t]),
                     "lo": float(b[t] - crit * se[t]), "hi": float(b[t] + crit * se[t]),
                     "cond": cond, "rank": rank, "n_terms": len(xs)})
    other = [("expo", expo)] + [(f"dmg_k{k}", f"dmg_{tag(k)}") for k in ks] + [(c, c) for c in controls]
    for name, col in other:
        if col in b.index:
            rows.append({**common, "k": np.nan, "term": name, "b": float(b[col]), "se": float(se[col]),
                         "p": float(p[col]), "lo": np.nan, "hi": np.nan})
        else:
            rows.append({**common, "k": np.nan, "term": name, "b": np.nan, "se": np.nan, "p": np.nan,
                         "lo": np.nan, "hi": np.nan, "absorbed": 1})
    return pd.DataFrame(rows)


def draw_dynamic(res, window, path, title):
    g0 = res[(res["window"] == window) & res["k"].notna()]
    fig, axes = plt.subplots(len(FIG_DVS), 2, figsize=(11, 3.3 * len(FIG_DVS)), sharex="col")
    for i, dv in enumerate(FIG_DVS):
        for j, d in enumerate(["current", "base"]):
            g = g0[(g0["design"] == d) & (g0["dv"] == dv)].sort_values("k")
            ax = axes[i, j]
            ax.axhline(0, color="0.35", lw=0.9)
            ax.axvline(0, color="0.8", lw=0.9, ls=":")
            ax.axvspan(1.5, g["k"].max() + 0.4, color="0.9", zorder=0)
            ax.errorbar(g["k"], g["b"], yerr=[g["b"] - g["lo"], g["hi"] - g["b"]],
                        fmt="o", color=RED, ecolor=BLUE, elinewidth=1.3, capsize=3, ms=5)
            ax.set_ylabel(DV_LABEL[dv], fontsize=9)
            if i == len(FIG_DVS) - 1:
                ax.set_xlabel("k: damage in year t+k   (shaded: damage after the violations)")
            label = "exposure in year t" if d == "current" else "exposure in the first year"
            ax.set_title(f"{label}  ({g['years'].iloc[0]}, N = {int(g['N'].iloc[0]):,})", fontsize=10)
            ax.set_xticks(sorted(g["k"].astype(int)))
            ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle(title + "\noutcome measured in t+1; shaded: damage arriving after the violations; "
                 "95% CI clustered by insurer", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=200)
    plt.close(fig)


def figure2(panel, out):
    res = pd.concat([dynamic(panel, dv, w, d) for w in (3, 2) for d in ("current", "base") for dv in FIG_DVS],
                    ignore_index=True)
    draw_dynamic(res, 3, out / "figure2_dynamic.png",
                 "Figure 2  Dynamic effect, window of plus or minus 3 years: all terms in the same regression")
    draw_dynamic(res, 2, out / "figure2_dynamic_w2.png",
                 "Figure 2 (continued)  Dynamic effect, window of plus or minus 2 years: all terms in the same regression")
    return res


def with_leads(panel, col, window):
    sy = panel.drop_duplicates(["state_postal", "year"])[["state_postal", "year", col]]
    lead = sy[["state_postal", "year"]].copy()
    for k in range(-window, window + 1):
        s = sy.copy()
        s["year"] = s["year"] - k
        lead = lead.merge(s.rename(columns={col: f"dmg_{tag(k)}"}), on=["state_postal", "year"], how="left")
    return panel[panel["in_sample_ms"] == 1].merge(lead, on=["state_postal", "year"], how="left")


def anticipation_checks(panel, dv, window=3):
    """The k = +2 term with past violations, without Texas and Washington, and with state trends."""
    df = with_leads(panel, DAMAGE, window)
    ks = list(range(-window, window + 1))
    xs = [tag(k) for k in ks]
    dmg = [f"dmg_{tag(k)}" for k in ks]
    for k in ks:
        df[tag(k)] = df[EXPO] * df[f"dmg_{tag(k)}"]
    df["trend"] = df["year"] - df["year"].min()
    # one state's slope is left out: the 51 slopes add up to a common trend
    states = sorted(df["state_postal"].unique())
    trends = [f"tr_{st}" for st in states[1:]]
    for st, c in zip(states[1:], trends):
        df[c] = df["trend"] * (df["state_postal"] == st)
    df["past1"], df["past2"] = df["count_t0"], df["count_l1"]
    need = [dv, EXPO] + xs
    checks = {
        "baseline (F7)": (need, xs + [EXPO], FE["F7"], df),
        "+ past violations (F7)": (need + ["past1", "past2"], xs + [EXPO, "past1", "past2"], FE["F7"], df),
        "without TX and WA (F7)": (need, xs + [EXPO], FE["F7"], df[~df["state_postal"].isin(["TX", "WA"])]),
        "F9 without trends": (need, xs + [EXPO] + dmg, FE["F9"], df),
        "F9 + state trends": (need, xs + [EXPO] + dmg + trends, FE["F9"], df),
    }
    rows = []
    for name, (cols, rhs, fes, d) in checks.items():
        d = d[d[cols].notna().all(axis=1)]
        m = feols(d, dv, rhs, fes)
        b, se, p = m.coef(), m.se(), m.pvalue()
        for k in ks:
            rows.append({"check": name, "dv": dv, "k": k, "term": tag(k), "b": float(b[tag(k)]),
                         "se": float(se[tag(k)]), "p": float(p[tag(k)]), "N": m._N})
        # the other terms: exposure, the past violations, and the damage terms themselves where
        # there are no state x year effects to absorb them
        other = [("expo", EXPO), ("past1", "past1"), ("past2", "past2")]
        other += [(f"dmg_k{k}", f"dmg_{tag(k)}") for k in ks]
        for label, col in other:
            if col in b.index:
                rows.append({"check": name, "dv": dv, "k": np.nan, "term": label, "b": float(b[col]),
                             "se": float(se[col]), "p": float(p[col]), "N": m._N})
        n_tr = sum(c in b.index for c in trends)
        if n_tr:
            rows.append({"check": name, "dv": dv, "k": np.nan, "term": f"{n_tr} state trends",
                         "b": np.nan, "se": np.nan, "p": np.nan, "N": m._N})
    return pd.DataFrame(rows)


def change_dynamic(panel, window=2):
    """The same lead and lag check for the change in damage."""
    df = with_leads(panel, "dlndmg_fire", window)
    ks = list(range(-window, window + 1))
    xs = [tag(k) for k in ks]
    for k in ks:
        df[tag(k)] = df[EXPO] * df[f"dmg_{tag(k)}"]
    rows = []
    for sample, d in [("full", df), ("without TX and WA", df[~df["state_postal"].isin(["TX", "WA"])])]:
        for dv in FIG_DVS:
            dd = d[d[[dv, EXPO] + xs].notna().all(axis=1)]
            m = feols(dd, dv, xs + [EXPO], FE["F7"])
            b, se, p = m.coef(), m.se(), m.pvalue()
            years = f"{int(dd['year'].min())}-{int(dd['year'].max())}"
            for k, col, term in [(k, tag(k), tag(k)) for k in ks] + [(np.nan, EXPO, "expo")]:
                rows.append({"sample": sample, "dv": dv, "k": k, "term": term, "b": float(b[col]),
                             "se": float(se[col]), "p": float(p[col]), "N": m._N, "years": years})
    return pd.DataFrame(rows)


# checks on Figure 2: (name, variant, fixed effects, damage, controls)
CHECK_DAMAGE = [("lndmg_fire", "Ln wildfire damage (Figure 2)"), ("dmgany_fire", "Any damage (above the median)"),
                ("dmgp75_fire", "Above the 75th percentile"), ("dmgp90_fire", "Above the 90th percentile"),
                ("dmg10m_fire", "Above USD 10 million"), ("dmg100m_fire", "Above USD 100 million")]
CHECK_CONTROLS = [((), "No controls (Figure 2)"), (("lnsize",), "Ln insurer size"),
                  (("lnsize", "hhi_fire4_t"), "Ln insurer size + HHI"),
                  (("lnsize", "x_hhi"), "Ln insurer size + Market share × HHI")]


def figure2_checks(panel):
    specs = []
    for controls, label in CHECK_CONTROLS:
        specs.append(("controls", label, "F7", DAMAGE, controls))
    for fe in FE:
        label = FE_LABEL[fe] + (" (Figure 2)" if fe == "F7" else "")
        specs.append(("fixed effects", label, fe, DAMAGE, ()))
        specs.append(("fixed effects, controls", FE_LABEL[fe], fe, DAMAGE, ("lnsize", "hhi_fire4_t")))
    for damage, label in CHECK_DAMAGE:
        specs.append(("damage dummies", label, "F7", damage, ()))
    specs.append(("corrected damage", "Repeated records counted once", "F7", "lndmg_fire_corr", ()))
    out = []
    for check, label, fe, damage, controls in specs:
        for w in (3, 2):
            for d in ("current", "base"):
                for dv in FIG_DVS:
                    r = dynamic(panel, dv, w, d, fe, damage, controls)
                    out.append(r.assign(check=check, variant=label, fe=fe, damage=damage,
                                        controls=" + ".join(controls) or "none"))
    return pd.concat(out, ignore_index=True)


def draw_checks(chk, path):
    """t statistics of the k = 0 and k = +2 terms across the checks (window of 3 years, exposure in t)."""
    g = chk[(chk["window"] == 3) & (chk["design"] == "current") & chk["k"].isin([0, 2])].copy()
    g["t"] = g["b"] / g["se"]
    groups = [("controls", "Controls"), ("fixed effects", "Fixed effects"),
              ("fixed effects, controls", "Fixed effects, with ln size and HHI"),
              ("damage dummies", "Damage measure"), ("corrected damage", "Corrected damage")]
    items = []                                  # (label, check, variant); check None for a group title
    for check, title in groups:
        items.append((title, None, None))
        items += [(v, check, v) for v in g.loc[g["check"] == check, "variant"].drop_duplicates()]
    ypos = np.arange(len(items))[::-1]
    fig, axes = plt.subplots(1, 3, figsize=(13, 0.3 * len(items) + 1.4), sharey=True)
    for j, dv in enumerate(FIG_DVS):
        ax = axes[j]
        for k, color, mk in [(0, BLUE, "o"), (2, RED, "s")]:
            pts = [(y, g[(g["check"] == c) & (g["variant"] == v) & (g["dv"] == dv) & (g["k"] == k)]["t"].iloc[0])
                   for y, (_, c, v) in zip(ypos, items) if c is not None]
            ax.scatter([t for _, t in pts], [y for y, _ in pts], color=color, marker=mk, s=22, zorder=3,
                       label="k = 0 (damage in t)" if k == 0 else "k = +2 (damage in t+2)")
        for x in (-1.96, 1.96):
            ax.axvline(x, color="0.6", lw=0.8, ls="--")
        ax.axvline(0, color="0.35", lw=0.8)
        for y, (_, c, _) in zip(ypos, items):
            if c is None:
                ax.axhline(y, color="0.92", lw=7, zorder=0)
        ax.set_title(DV_LABEL[dv], fontsize=10)
        ax.set_xlabel("t statistic")
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_yticks(ypos)
    axes[0].set_yticklabels([lab for lab, _, _ in items], fontsize=8)
    for tick, (_, c, _) in zip(axes[0].get_yticklabels(), items):
        if c is None:
            tick.set_fontweight("bold")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.955))
    fig.suptitle("Figure 3  Checks on Figure 2: t statistics of the terms at k = 0 and k = +2\n"
                 "window of plus or minus 3 years, exposure in year t; dashed lines at ±1.96", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=200)
    plt.close(fig)


# ----------------------------------------------------------------------------- damage check

def damage_check(panel, corr, rep, fires, master, sources, n=10):
    """The largest state-year wildfire damages, the records behind them, what published sources
    report, and the fire-line loss ratio of the state market around the year."""
    f = fires.copy()
    f["dmg"] = f["DAMAGE_PROPERTY"].map(dollars) + f["DAMAGE_CROPS"].map(dollars)
    names = panel.drop_duplicates("state_postal").set_index("state_name_uc")["state_postal"]
    f["state_postal"] = f["STATE"].map(names)
    f["year"] = f["YEAR"].astype(int)

    fl = master[(master["territory"] == 0) & (master["lg_fire4"] == 1)]
    lr = (fl.assign(pe=fl["premiums_earned"].fillna(0).clip(lower=0), li=fl["losses_incurred"].fillna(0))
          .groupby(["state_postal", "year"])[["pe", "li"]].sum())
    lr = (lr["li"] / lr["pe"]).rename("lr")
    years = lr.index.get_level_values("year")
    lr = lr[(years >= FIRST_YEAR) & (years <= LAST_MS_YEAR)]

    top = corr.sort_values("dmg24_fire", ascending=False).head(n)
    src = sources.assign(year=sources["year"].astype(int)).set_index(["state", "year"])
    rows = []
    for rank, (_, r) in enumerate(top.iterrows(), 1):
        st, yr = r["state_postal"], int(r["year"])
        recs = f[(f["state_postal"] == st) & (f["year"] == yr) & (f["dmg"] > 0)]
        recs = recs.assign(start=recs["BEGIN_YEARMONTH"].astype(int) * 100 + recs["BEGIN_DAY"].astype(int))
        # the largest record that is not itself a monthly repeat, so the date shown is the
        # month in which that fire's record first carries the damage
        kept = recs[~recs["EVENT_ID"].isin(set(rep["event_id"]))]
        main = (kept if len(kept) else recs).sort_values(["dmg", "start"], ascending=[False, True]).iloc[0]
        pub = src.loc[(st, yr)] if (st, yr) in src.index else None
        lrs = {k: lr.get((st, yr + k), np.nan) for k in (-1, 0, 1)}
        others = lr[(lr.index.get_level_values("state_postal") == st)]
        rows.append({"rank": rank, "state": st, "year": yr,
                     "damage_usd_m": r["dmg24_fire"] / 1e6, "records_with_damage": len(recs),
                     "largest_record_begins": main["BEGIN_DATE_TIME"][:9],
                     "largest_record_usd_m": main["dmg"] / 1e6 * r["dmg24_fire"] / r["dmgnom_fire"],
                     "fire": pub["fire"] if pub is not None else "",
                     "fire_began": pub["began"] if pub is not None else "",
                     "published_estimate": pub["published_estimate"] if pub is not None else "",
                     "source": pub["source"] if pub is not None else "",
                     "repeated_usd_m": r["repeated_24"] / 1e6,
                     "corrected_usd_m": r["dmg24_fire_corr"] / 1e6,
                     "loss_ratio_t_minus_1": lrs[-1], "loss_ratio_t": lrs[0], "loss_ratio_t_plus_1": lrs[1],
                     "loss_ratio_median_2014_2023": float(others[others.index.get_level_values("year") >= FIRST_YEAR].median())})
    top = pd.DataFrame(rows)

    changed = corr[corr["repeated_nom"] > 0].copy()
    changed["records"] = changed.apply(lambda r: int(((rep["state"].map(names) == r["state_postal"])
                                                       & (rep["year"] == r["year"])).sum()), axis=1)
    changed["damage_usd_m"] = changed["dmg24_fire"] / 1e6
    changed["repeated_usd_m"] = changed["repeated_24"] / 1e6
    changed["corrected_usd_m"] = changed["dmg24_fire_corr"] / 1e6
    changed = changed.sort_values("repeated_usd_m", ascending=False)[
        ["state_postal", "year", "records", "damage_usd_m", "repeated_usd_m", "corrected_usd_m"]]

    # the opposite error: the state's fire-line market paid out far more than usual, and more than
    # it earned, in a year Storm Events records almost no wildfire damage
    d = lr.rename("lr").reset_index()
    d = d.merge(corr[["state_postal", "year", "dmg24_fire"]], on=["state_postal", "year"])
    d["lr_median"] = d.groupby("state_postal")["lr"].transform("median")
    d["damage_usd_m"] = d["dmg24_fire"] / 1e6
    low = d[(d["lr"] > 1) & (d["lr"] > 1.5 * d["lr_median"]) & (d["damage_usd_m"] < 200)]
    low = low.sort_values("lr", ascending=False)[
        ["state_postal", "year", "lr", "lr_median", "damage_usd_m"]]
    return top, changed, low


# ----------------------------------------------------------------------------- workbook

FONT = "Times New Roman"
F_BASE = Font(name=FONT, size=11)
F_BOLD = Font(name=FONT, size=11, bold=True)
F_TITLE = Font(name=FONT, size=12, bold=True)
F_ITAL = Font(name=FONT, size=11, italic=True)
F_NOTE = Font(name=FONT, size=10)
F_NOTE_I = Font(name=FONT, size=10, italic=True)
THIN, MED = Side(style="thin"), Side(style="medium")
CENTER = Alignment(horizontal="center", vertical="center")
LEFT = Alignment(horizontal="left", vertical="center")
WRAP = Alignment(horizontal="left", vertical="top", wrap_text=True)
CENTER_WRAP = Alignment(horizontal="center", vertical="center", wrap_text=True)

DV_NAME = {"count": "Number of violations", "lnpen": "Ln(1 + penalties)", "any": "Any violation",
           "lossburden": "Losses in the exposed lines (year t)"}
FE_ROWS = [("Insurer FE", {"F3"}), ("State FE", {"F3", "F5"}), ("Year FE", {"F3", "F11"}),
           ("Insurer × Year FE", {"F5", "F9", "F10"}),
           ("Insurer × State FE", {"F7", "F9", "F10", "F11"}), ("State × Year FE", {"F7", "F10"})]
GROUP = {"fire4": "Fire lines", "fireonly": "Fire only", "firehome": "Fire and homeowners",
         "firebroad": "Broad fire lines", "prop5": "Property lines", "privflood": "Private flood", "crop": "Crop"}
HAZARD = {"fire": "wildfire", "all": "all hazards", "flood": "floods", "tropical": "tropical cyclones",
          "convective": "convective storms", "wind": "other wind", "winter": "winter storms",
          "drought_heat": "drought and heat"}
MOD = {"low_state": "Low profitability of the state market (first year)",
       "low_is": "Low profitability of the insurer in the state (first year)",
       "tight_reg_free": "Tight regulation: underwriting freedom, as printed",
       "tight_reg_free_rc": "Tight regulation: underwriting freedom, rebuilt from components",
       "tight_reg_rate": "Tight regulation: rate regulation",
       "tight_home": "Tight regulation: homeowners rate regulation",
       "fewins_fire4_first": "Few insurers (first year)", "hitop3_fire4_first": "High top-3 share (first year)",
       "hitop5_fire4_first": "High top-5 share (first year)", "fewins_fire4_l1": "Few insurers (t-1)",
       "hitop3_fire4_l1": "High top-3 share (t-1)", "hitop5_fire4_l1": "High top-5 share (t-1)",
       "fewins_fire4_t": "Few insurers (year t)", "hitop3_fire4_t": "High top-3 share (year t)"}
SCALE = {"hhi_fire4_t": 1000.0}
STARS_NOTE = "Standard errors clustered by insurer in parentheses. *** p<0.01, ** p<0.05, * p<0.1."
MAIN_NOTE = ("Exposure and damage enter every regression on their own; 'absorbed' marks a term that the fixed "
             "effects absorb. ")


def star(p):
    return "***" if p < 0.01 else "**" if p < 0.05 else "*" if p < 0.1 else ""


def pick(df, filt):
    s = df
    for k, v in filt.items():
        s = s[s[k] == v]
    if len(s) > 1:
        raise ValueError(f"{len(s)} rows match {filt}")
    return None if s.empty else s.iloc[0]


class Sheet:
    def __init__(self, wb, name, ncols, label_width=46, col_width=12):
        self.ws = wb.create_sheet(name)
        self.ws.sheet_view.showGridLines = False
        self.ncols = ncols
        self.cur = ncols
        self.r = 1
        self.ws.column_dimensions["A"].width = label_width
        for c in range(2, ncols + 2):
            self.ws.column_dimensions[get_column_letter(c)].width = col_width
        self.ws.page_setup.orientation = "landscape"
        self.ws.page_setup.fitToWidth = 1
        self.ws.page_setup.fitToHeight = 0
        self.ws.sheet_properties.pageSetUpPr.fitToPage = True

    def put(self, row, col, value, font=F_BASE, align=CENTER):
        c = self.ws.cell(row=row, column=col, value=value)
        c.font, c.alignment = font, align
        return c

    def merged(self, text, font=F_BASE, align=WRAP, height=None, last=None):
        last = last or self.ncols + 1
        self.ws.merge_cells(start_row=self.r, start_column=1, end_row=self.r, end_column=last)
        self.put(self.r, 1, text, font, align)
        if height:
            self.ws.row_dimensions[self.r].height = height
        self.r += 1

    def h(self, text):
        cpl = max(60, int(sum(self.ws.column_dimensions[get_column_letter(c)].width
                              for c in range(1, self.ncols + 2)) * 1.15))
        lines = sum(max(1, -(-len(part) // cpl)) for part in str(text).split("\n"))
        return 14 * lines + 4

    def title(self, head, text, desc=None):
        self.merged(f"{head}. {text}", F_TITLE, LEFT, height=20)
        if desc:
            self.merged(desc, F_NOTE, WRAP, height=self.h(desc))
        self.r += 1

    def notes(self, text):
        self.merged("Notes: " + text, F_NOTE, WRAP, height=self.h("Notes: " + text))
        self.r += 1

    def rule(self, row, side, top=True):
        for c in range(1, self.cur + 2):
            cell = self.ws.cell(row=row, column=c)
            b = cell.border
            cell.border = Border(top=side if top else b.top, bottom=b.bottom if top else side,
                                 left=b.left, right=b.right)

    def header(self, cols):
        self.cur = len(cols)
        self.rule(self.r, MED, top=True)
        c = 2
        while c - 2 < len(cols):
            dv = cols[c - 2]["dv"]
            span = 1
            while c - 2 + span < len(cols) and cols[c - 2 + span]["dv"] == dv:
                span += 1
            if span > 1:
                self.ws.merge_cells(start_row=self.r, start_column=c, end_row=self.r, end_column=c + span - 1)
            self.put(self.r, c, DV_NAME.get(dv, dv), F_BASE, CENTER_WRAP)
            for k in range(c, c + span):
                self.ws.cell(row=self.r, column=k).border = Border(bottom=THIN, top=MED)
            c += span
        self.ws.row_dimensions[self.r].height = 30
        self.r += 1
        for i in range(len(cols)):
            self.put(self.r, i + 2, f"({i + 1})")
        self.rule(self.r, THIN, top=False)
        self.r += 1

    def coef_rows(self, label, cells, scale=1.0):
        self.put(self.r, 1, label, F_BASE, LEFT)
        for i, x in enumerate(cells):
            if x is None:
                continue
            if x.get("absorbed") == 1:
                self.put(self.r, i + 2, "absorbed", F_ITAL)
            else:
                self.put(self.r, i + 2, f"{x['b'] * scale:.4f}{star(x['p'])}")
                self.put(self.r + 1, i + 2, f"({x['se'] * scale:.4f})")
        self.r += 2

    def value_row(self, label, values, font=F_BASE):
        self.put(self.r, 1, label, font, LEFT)
        for i, v in enumerate(values):
            if v is not None:
                self.put(self.r, i + 2, v, font)
        self.r += 1

    def panel_title(self, text):
        self.put(self.r, 1, text, F_ITAL, LEFT)
        self.r += 1

    def fe_block(self, cols):
        self.rule(self.r, THIN, top=True)
        fes = [c["fe"] for c in cols]
        for name, sets in FE_ROWS:
            if any(f in sets for f in fes):
                self.value_row(name, ["Yes" if f in sets else "No" for f in fes])
        self.rule(self.r, MED, top=True)
        self.r += 1


def n_of(x):
    return int(x["N"]) if x is not None and x.get("absorbed") != 1 and pd.notna(x.get("N")) else None


def regression_block(sh, res, cols, panels):
    """panels: list of (title, blocks); a block is the rows (label, filter, term) of one regression.
    The number of observations goes once under a panel when it is the same for all its blocks,
    and under every block otherwise."""
    sh.header(cols)
    for j, (title, blocks) in enumerate(panels):
        if title:
            if j:
                sh.r += 1
            sh.panel_title(title)
        grid = [[[pick(res, {**filt, **c, "term": term}) for c in cols] for _, filt, term in block]
                for block in blocks]
        ns = [[n_of(b[0][i]) for b in grid] for i in range(len(cols))]
        per_block = any(len({n for n in col if n is not None}) > 1 for col in ns)
        for block, cells in zip(blocks, grid):
            for (label, _, term), row in zip(block, cells):
                sh.coef_rows(label, row, scale=SCALE.get(term, 1.0))
            if per_block:
                sh.value_row("    Observations", [f"{n_of(x):,}" if n_of(x) else None for x in cells[0]], F_NOTE_I)
        if not per_block:
            sh.value_row("Observations", [f"{n_of(x):,}" if n_of(x) else None for x in grid[0][0]])
    sh.fe_block(cols)


def cols_for(fes, dvs=DVS, **extra):
    return [{"dv": dv, "fe": fe, **extra} for dv in dvs for fe in fes]


def simple_table(sh, header, rows, first_left=True, all_left=False, height=30):
    sh.cur = len(header) - 1
    sh.rule(sh.r, MED, top=True)
    for i, h in enumerate(header):
        sh.put(sh.r, i + 1, h, F_BASE, LEFT if ((i == 0 and first_left) or all_left) else CENTER_WRAP)
    sh.rule(sh.r, THIN, top=False)
    sh.ws.row_dimensions[sh.r].height = height
    sh.r += 1
    for row in rows:
        for i, v in enumerate(row):
            sh.put(sh.r, i + 1, v, F_BASE, LEFT if ((i == 0 and first_left) or all_left) else CENTER)
        sh.r += 1
    sh.rule(sh.r, MED, top=True)
    sh.r += 1


def add_image(sh, path, width_px):
    img = XLImage(str(path))
    ratio = width_px / img.width
    img.width, img.height = width_px, int(img.height * ratio)
    sh.ws.add_image(img, f"A{sh.r}")
    sh.r += int(img.height / 20) + 2


def three(inter, expo, dmg, filt, indent=""):
    """The interaction and its two lower-order terms."""
    return [(inter, filt, "x"), (f"{indent}{expo}", filt, "expo"), (f"{indent}{dmg}", filt, "dmg")]


def moderator_rows(sub, cols, sh, power_rows=None):
    for label, term in [("Exposure × Damage", "x"), ("Exposure × Damage × Moderator", "x_m"),
                        ("Damage × Moderator", "d_m"), ("Exposure × Moderator", "e_m"), ("Moderator", "mod"),
                        ("Exposure", "expo"), ("Damage", "dmg"), ("Ln insurer size", "lnsize"),
                        ("Effect when Moderator = 1", "x_sum")]:
        sh.coef_rows(label, [pick(sub, {**c, "term": term}) for c in cols])
    sh.value_row("Observations", [f"{int(pick(sub, {**c, 'term': 'x'})['N']):,}" for c in cols])
    if power_rows is not None:
        sh.value_row("MDE / baseline effect", [f"{pick(power_rows, c)['mde_over_baseline']:.1f}" for c in cols], F_NOTE_I)


def workbook(out, res, summ, notes, splits, defs, power_tab, cells, f1, t10, f2, ant, f2c, chk, mx,
             top, changed, low, corr):
    res = res.copy()
    notes = notes.set_index("hazard")
    splits = splits.set_index("measure")
    T = lambda t: res[res["table"] == t]

    wb = Workbook()
    wb.remove(wb.active)

    # ---- contents
    sh = Sheet(wb, "Contents", 1, label_width=16, col_width=96)
    sh.merged("Disaster exposure and regulatory violations of US property and casualty insurers", F_TITLE, LEFT, 20)
    sh.merged("Tables and figures, 9 October 2026", F_ITAL, LEFT)
    sh.r += 1
    contents = [("Table 1", "Summary statistics"), ("Table 2", "Baseline regressions"),
                ("Table 3", "Alternative definitions of the exposed lines"),
                ("Table 4", "Alternative definitions of exposure and of damage"),
                ("Table 5", "Other dependent variables"),
                ("Table 6", "Moderating effect of ex-ante profitability"),
                ("Table 7", "Moderating effect of regulatory friction"),
                ("Table 8", "Moderating effect of consumers' ability to switch insurer"),
                ("Appendix 1", "Definitions of the variables"),
                ("Appendix 2", "Alternative clustering of the standard errors"),
                ("Appendix 3", "Contemporaneous exposure, losses in the exposed lines, and contemporaneous market structure"),
                ("Appendix 4", "Placebo: hazards other than wildfire"),
                ("Appendix 5", "Baseline without Texas and Washington"),
                ("Appendix 6", "Check of the wildfire damage data"),
                ("Appendix 7", "Where the term at k = +2 of Figure 2 comes from, with Figure 4"),
                ("Figure 1", "Placebo tests"), ("Figure 2", "Dynamic effect"),
                ("Figure 3", "Checks on the dynamic effect")]
    simple_table(sh, ["Sheet", "Content"], contents, all_left=True)
    sh.merged("Sample: insurer × state × year, 38 insurer groups linked to Violation Tracker, 51 states (including DC), "
              "2013-2024. Data: S&P Capital IQ statutory premiums and losses; Violation Tracker penalties; NOAA Storm "
              "Events property and crop damage (USD of 2024). All regressions are OLS; standard errors are clustered by "
              "insurer unless stated otherwise. In every regression with an interaction, its lower-order terms are "
              "included and reported, unless the fixed effects absorb them. Each table states its own sample.",
              F_NOTE, WRAP, height=62, last=2)

    # ---- Table 1
    sh = Sheet(wb, "Table 1", 7, label_width=62, col_width=12)
    sh.title("Table 1", "Summary statistics",
             "This table reports summary statistics for every variable used in the tables and figures. Each variable "
             "is described on the sample in which it enters the regressions: outcomes at t+2 and t+3 only for exposure "
             "years whose outcome year is observed (up to 2024); first-year and lagged exposure and the moderators on "
             "the years after an insurer-state's first year. Panel A is the market-share sample; Panel B adds the "
             "portfolio-share exposures of the portfolio-share sample.")
    head = ["Variable", "N", "Mean", "SD", "P25", "Median", "P75"]

    def stat_rows(df):
        return [[r["label"] or r["variable"], f"{int(r['N']):,}", f"{r['mean']:.4f}", f"{r['sd']:.4f}",
                 f"{r['p25']:.4f}", f"{r['median']:.4f}", f"{r['p75']:.4f}"] for _, r in df.iterrows()]
    sh.panel_title("Panel A. Market-share sample")
    simple_table(sh, head, stat_rows(summ[(summ["sample"] == "mshare") & ~summ["variable"].str.endswith("_ins_f1")]))
    sh.panel_title("Panel B. Portfolio-share sample: exposure variables")
    simple_table(sh, head, stat_rows(summ[(summ["sample"] == "share") & summ["variable"].str.startswith("share_")]))
    sh.notes("Definitions and sources in Appendix 1. Damage in USD million of 2024. The insurance-violation outcomes "
             "at t+1 used in Table 5 are the same variables as the Table 2 outcomes and are listed once.")

    # ---- Table 2
    sh = Sheet(wb, "Table 2", 9)
    sh.title("Table 2", "Baseline regressions",
             "This table reports OLS estimates of insurance violations in year t+1 on the insurer's exposure to the "
             "fire lines in year t interacted with wildfire damage in the state in year t.")
    t2 = T("2")
    C0, C1 = {"panel": "C", "controls": "none"}, {"panel": "C", "controls": "size_hhi"}
    regression_block(sh, t2, cols_for(MAIN_FE), [
        ("Panel A. Market share", [three("Market share × Ln wildfire damage", "Market share", "Ln wildfire damage", {"panel": "A"})]),
        ("Panel B. Portfolio share", [three("Portfolio share × Ln wildfire damage", "Portfolio share", "Ln wildfire damage", {"panel": "B"})]),
        ("Panel C. Market share, 2014-2023, without controls",
         [three("Market share × Ln wildfire damage", "Market share", "Ln wildfire damage", C0)]),
        ("Panel C (continued). With controls",
         [three("Market share × Ln wildfire damage", "Market share", "Ln wildfire damage", C1)
          + [("Ln insurer size", C1, "lnsize"), ("HHI, fire lines (year t), per 1,000 points", C1, "hhi_fire4_t")]]),
        ("Panel D. Change in damage", [three("Market share × Δ Ln wildfire damage", "Market share", "Δ Ln wildfire damage", {"panel": "D"})]),
    ])
    fire = notes.loc["fire"]
    sh.notes("Ln wildfire damage is ln(1 + property and crop damage from wildfire in the state, USD million of 2024). "
             "Wildfire damage varies by state and year, so state × year effects absorb it in columns (3), (6) and (9). "
             "Panel B excludes 2024, whose outcome year 2025 is only partly filed. Panel C adds ln insurer size in the "
             "state and the fire-line Herfindahl index of the year; because the index is not defined in 2013, both "
             "regressions of Panel C run on 2014-2023. The index is absorbed by state × year effects. Panel D replaces "
             "the level with its one-year change, ln(1 + D(t)) - ln(1 + D(t-1)), defined at zero damage and missing only "
             f"in 2013; it is zero in {fire['share_zero_change']:.0%} of state-years. " + MAIN_NOTE + STARS_NOTE)

    # ---- Table 3
    sh = Sheet(wb, "Table 3", 9, label_width=54)
    sh.title("Table 3", "Alternative definitions of the exposed lines",
             "This table repeats Table 2, Panel A, with exposure measured in other line groups, each paired with "
             "damage from the hazard that threatens it.")
    t3 = T("3")
    pairs = list(t3[["group", "hazard"]].drop_duplicates().itertuples(index=False))
    regression_block(sh, t3, cols_for(MAIN_FE), [(None, [
        three(f"{GROUP[g]} market share × Ln damage ({HAZARD[h]})", f"{GROUP[g]} market share", f"Ln damage ({HAZARD[h]})",
              {"group": g, "hazard": h}, indent="    ") for g, h in pairs])])
    sh.notes("Line groups: fire lines = fire, homeowners, farmowners and commercial multiple peril (non-liability); "
             "fire only; fire and homeowners; broad fire lines = fire lines plus allied lines, inland marine and auto "
             "physical damage; property lines = fire lines plus allied lines; private flood; crop. Each block of three "
             "rows is one regression. " + MAIN_NOTE + STARS_NOTE)

    # ---- Table 4
    sh = Sheet(wb, "Table 4", 9, label_width=50)
    sh.title("Table 4", "Alternative definitions of exposure and of damage",
             "Panels A-D measure exposure in the first year of the sample or in t-1, with ln damage or its change. "
             "Panels E-F replace ln damage with damage dummies. Panel G enters the current and the lagged level of "
             "damage together.")
    t4 = T("4")
    regression_block(sh, t4, cols_for(MOD_FE), [
        ("Panel A. First-year exposure × Ln damage",
         [three("First-year market share × Ln wildfire damage", "First-year market share", "Ln wildfire damage", {"panel": "A"})]),
        ("Panel B. Exposure in t-1 × Ln damage",
         [three("Market share in t-1 × Ln wildfire damage", "Market share in t-1", "Ln wildfire damage", {"panel": "B"})]),
        ("Panel C. First-year exposure × change in damage",
         [three("First-year market share × Δ Ln wildfire damage", "First-year market share", "Δ Ln wildfire damage", {"panel": "C"})]),
        ("Panel D. Exposure in t-1 × change in damage",
         [three("Market share in t-1 × Δ Ln wildfire damage", "Market share in t-1", "Δ Ln wildfire damage", {"panel": "D"})]),
    ])
    measures = [("lndmg", "Ln wildfire damage"), ("dmgany", "Any wildfire damage"),
                ("dmgp75", "Wildfire damage in the top quartile"), ("d_dmgp75", "Δ Top-quartile dummy")]
    regression_block(sh, t4, cols_for(MAIN_FE), [
        ("Panel E. Damage dummies, exposure in year t",
         [three(f"Market share × {lab}", "Market share", lab, {"panel": "E", "damage": m}, indent="    ")
          for m, lab in measures])])
    regression_block(sh, t4, cols_for(["F7"]), [
        ("Panel F. Damage dummies, first-year exposure",
         [three(f"First-year market share × {lab}", "First-year market share", lab, {"panel": "F", "damage": m}, indent="    ")
          for m, lab in measures])])
    G = {"panel": "G"}
    regression_block(sh, t4, cols_for(MAIN_FE), [
        ("Panel G. Current and lagged level of damage", [[
            ("Market share × Ln wildfire damage (t)", G, "x"), ("Market share × Ln wildfire damage (t-1)", G, "x_l"),
            ("Sum of the two", G, "x_sum"), ("Market share", G, "expo"), ("Ln wildfire damage (t)", G, "dmg"),
            ("Ln wildfire damage (t-1)", G, "dmg_l1")]])])
    usd = fire["p75_usd_million"] * 1e6
    sh.notes("First-year exposure is the market share in 2014, or in an insurer-state's first later year with "
             "premiums; it is constant within an insurer-state, so insurer × state effects absorb it, and the regressions "
             f"use the years after it. Wildfire damage is zero in {fire['share_zero_level']:.0%} of state-years, so its "
             "median is zero and the above-median dummy equals the any-damage dummy; the 75th percentile is about USD "
             f"{round(usd, -3):,.0f}. Δ Top-quartile dummy is the one-year change in the top-quartile dummy. In Panel G a "
             "lagged term near zero indicates a level effect and a sum near zero a change effect. In Panels E-F each "
             "block of three rows is one regression. " + MAIN_NOTE + STARS_NOTE)

    # ---- Table 5
    sh = Sheet(wb, "Table 5", 9)
    sh.title("Table 5", "Other dependent variables",
             "This table repeats Table 2, Panel A, for insurance violations in t+1, t+2 and t+3 (Panel A) and for "
             "violations other than insurance violations (Panel B).")
    t5 = T("5")
    regression_block(sh, t5, cols_for(MAIN_FE), [
        (f"Panel {p}. {name}", [three(f"Market share × Ln wildfire damage, outcome in t+{h}", "Market share",
                                      "Ln wildfire damage", {"panel": p, "horizon": h}, indent="    ") for h in [1, 2, 3]])
        for p, name in [("A", "Insurance violations"), ("B", "Non-insurance violations")]])
    sh.notes("Each horizon uses only exposure years whose outcome year is fully observed (up to 2024); violation "
             "records for 2025 are only partly filed. Only penalties imposed by an agency of one state can be placed "
             "in the insurer × state panel; federal cases, mostly railroad and workplace safety, cannot. Each block of "
             "three rows is one regression. " + MAIN_NOTE + STARS_NOTE)

    # ---- Tables 6-8
    def moderator_table(tnum, title, desc, extra_note):
        sh = Sheet(wb, f"Table {tnum}", 9)
        sh.title(f"Table {tnum}", title, desc)
        rt = T(tnum)
        cols = cols_for(MOD_FE)
        sh.header(cols)
        for j, mod in enumerate(rt["moderator"].dropna().unique()):
            if j:
                sh.r += 1
            sh.panel_title(f"Moderator: {MOD[mod]}")
            pw = power_tab[(power_tab["table"] == tnum) & (power_tab["moderator"] == mod)]
            moderator_rows(rt[rt["moderator"] == mod], cols, sh, pw)
        sh.fe_block(cols)
        ce = cells[cells["moderator"].isin(rt["moderator"].unique())]
        sh.notes(moderator_note(ce) + extra_note + " " + STARS_NOTE)

    def moderator_note(ce):
        return ("Exposure is the first-year market share of the fire lines; damage is ln(1 + wildfire damage). All "
                "regressions control for ln insurer size in the state. First-year exposure is constant within an "
                "insurer-state, and so are a moderator that is a fixed characteristic and its product with exposure: "
                "insurer × state effects absorb them. Damage, and damage × a moderator that varies by state only, are "
                "absorbed by state × year effects. MDE / baseline effect is the minimum detectable effect of the triple "
                "interaction at 80% power and a 5% two-sided test, divided by the effect in Table 4, Panel A (same "
                f"outcome and fixed effects, without the size control). The interaction is identified by "
                f"{int(ce['identifying_cells'].max())} insurer-state-years with a violation in t+1 and positive wildfire "
                f"damage (of {int(ce['sample_rows'].max()):,}); the smaller arm of a moderator has "
                f"{int(ce['smaller_arm'].min())} to {int(ce['smaller_arm'].max())} of them. ")

    moderator_table("6", "Moderating effect of ex-ante profitability",
                    "This table interacts the baseline effect with low profitability in the first year: a fire-line "
                    "loss ratio above the median, for the state market or for the insurer in the state.",
                    "Low profitability is a loss ratio (losses incurred over premiums earned, fire lines) above the "
                    "median in the first year.")
    moderator_table("7", "Moderating effect of regulatory friction",
                    "This table interacts the baseline effect with tight insurance regulation, from the R Street 2024 "
                    "Insurance Regulation Report Card, used as a fixed state characteristic.",
                    "The report scores regulatory freedom; a state is tight when its score is strictly below the median "
                    "of the 50 states, and a state exactly at the median counts as free "
                    f"(underwriting freedom: {int(splits.loc['reg_free', 'ties_at_median'])} states at the median, "
                    f"{int(splits.loc['reg_free', 'n_tight'])} tight; rate regulation: "
                    f"{int(splits.loc['reg_rate', 'ties_at_median'])} and {int(splits.loc['reg_rate', 'n_tight'])}; "
                    f"homeowners: {int(splits.loc['home', 'ties_at_median'])} and {int(splits.loc['home', 'n_tight'])}). "
                    "The report does not cover DC. Its own arithmetic fails for five states (Nebraska, Colorado, Nevada, "
                    "Washington, Louisiana); the rebuilt score sums the report's components and removes all five.")
    moderator_table("8", "Moderating effect of consumers' ability to switch insurer",
                    "This table interacts the baseline effect with captive consumers: few insurers writing the fire "
                    "lines in the state, or a large premium share held by the top three or top five.",
                    "Market structure is measured in 2014 (first year; median across the 51 states) or in t-1 (median "
                    "across states in the same year, so the dummy compares states within a year); never in the damage "
                    "year. A moderator measured in t-1 varies over time, so its product with exposure is identified, and "
                    "so is the moderator itself where there are no state × year effects. Market-structure data are "
                    "complete for 2014-2023 only.")

    # ---- Appendix 1
    sh = Sheet(wb, "Appendix 1", 3, label_width=24, col_width=40)
    sh.ws.column_dimensions["C"].width = 70
    sh.ws.column_dimensions["D"].width = 24
    sh.title("Appendix 1", "Definitions of the variables")
    sh.rule(sh.r, MED, top=True)
    for i, h in enumerate(["Variable", "Label", "Definition", "Source"]):
        sh.put(sh.r, i + 1, h, F_BOLD, LEFT)
    sh.rule(sh.r, THIN, top=False)
    sh.r += 1
    for v in ORDER:
        label, text, source = defs[v]
        for i, val in enumerate([v, label, text, source]):
            sh.put(sh.r, i + 1, val, F_NOTE, WRAP)
        sh.ws.row_dimensions[sh.r].height = max(16, 14 * (-(-len(text) // 80)) + 4)
        sh.r += 1
    sh.rule(sh.r, MED, top=True)

    # ---- Appendix 2
    sh = Sheet(wb, "Appendix 2", 9)
    sh.title("Appendix 2", "Alternative clustering of the standard errors",
             "This table repeats Table 2, Panel A, with standard errors clustered by insurer, by state, and two ways "
             "by insurer and state.")
    a2 = T("A2")
    regression_block(sh, a2, cols_for(MAIN_FE), [
        (f"Clustered by {name}", [three("Market share × Ln wildfire damage", "Market share", "Ln wildfire damage", {"cluster": cl})])
        for cl, name in [("insurer", "insurer"), ("state", "state"), ("twoway", "insurer and state")]])
    nc = {cl: int(a2[a2["cluster"] == cl]["clusters"].max()) for cl in ["insurer", "state"]}
    sh.notes(f"Insurer clusters: {nc['insurer']}; state clusters: {nc['state']}. " + MAIN_NOTE
             + "*** p<0.01, ** p<0.05, * p<0.1.")

    # ---- Appendix 3
    sh = Sheet(wb, "Appendix 3", 9, label_width=54)
    sh.title("Appendix 3", "Contemporaneous exposure, losses in the exposed lines, and contemporaneous market structure",
             "Panel A measures exposure in year t under insurer × state and insurer × year effects. Panel B regresses "
             "losses in the exposed lines in year t on exposure × damage. Panels C-D measure market structure in the "
             "damage year itself.")
    a3 = T("A3")
    sh.ncols = 6
    regression_block(sh, a3, cols_for(["F9", "F10"]), [
        ("Panel A. Exposure in year t", [three("Market share × Ln wildfire damage", "Market share", "Ln wildfire damage", {"panel": "A"})])])
    pb = a3[a3["panel"] == "B"]
    pairs = list(pb[["group", "hazard"]].drop_duplicates().itertuples(index=False))
    sh.ncols = 3
    regression_block(sh, pb, [{"dv": "lossburden", "fe": f} for f in MAIN_FE], [
        (f"Panel B{i}. {name}", [three(f"{GROUP[g]} {name.lower()} × Ln damage ({HAZARD[h]})", f"{GROUP[g]} {name.lower()}",
                                       f"Ln damage ({HAZARD[h]})", {"expo": e, "group": g, "hazard": h}, indent="    ")
                                 for g, h in pairs])
        for i, (e, name) in enumerate([("mshare", "Market share"), ("share", "Portfolio share")], 1)])
    sh.ncols = 9
    cols = cols_for(MOD_FE)
    sh.header(cols)
    for p in ["C", "D"]:
        if p == "D":
            sh.r += 1
        sub = a3[a3["panel"] == p]
        mod = sub["moderator"].dropna().iloc[0]
        sh.panel_title(f"Panel {p}. Moderator: {MOD[mod]}")
        moderator_rows(sub, cols, sh)
    sh.fe_block(cols)
    fx = mx[(mx["lines"] == "fire4") & (mx["outcome"] == "d_nins")].iloc[0]
    sh.notes("Panel B regresses losses incurred in the exposed lines over premiums earned in all lines, winsorised at "
             "1% and 99%, on exposure × damage in the same year; each block of three rows is one regression. Panels C-D "
             "are reported in this appendix because market structure should be measured before the event; the data show "
             "no sign that disasters drive insurers out (over 2014-2023, states gained on average "
             f"{fx['mean_after_high']:.1f} fire-line insurers after a year with wildfire damage and "
             f"{fx['mean_after_low']:.1f} after a year without; within-year difference "
             f"{fx['within_year_difference']:+.2f}, p = {fx['within_year_p']:.2f}). " + MAIN_NOTE + STARS_NOTE)

    # ---- Appendix 4
    sh = Sheet(wb, "Appendix 4", 9, label_width=50)
    sh.title("Appendix 4", "Placebo: hazards other than wildfire",
             "This table repeats Table 2, Panel A, with damage from hazards other than wildfire in place of "
             "wildfire damage.")
    regression_block(sh, T("A4"), cols_for(MAIN_FE), [(None, [
        three(f"Market share × Ln damage ({HAZARD[h]})", "Market share", f"Ln damage ({HAZARD[h]})", {"hazard": h}, indent="    ")
        for h in PLACEBO_HAZARDS])])
    sh.notes("These are imperfect placebos: the fire lines include homeowners and commercial property, which also "
             "cover wind, hail, winter-storm and hurricane-wind losses. Flood, which standard policies exclude, is "
             "the cleanest. Each block of three rows is one regression. " + MAIN_NOTE + STARS_NOTE)

    # ---- Appendix 5
    sh = Sheet(wb, "Appendix 5", 9)
    sh.title("Appendix 5", "Baseline without Texas and Washington",
             "This table repeats Table 2, Panels A and D, without the insurer-state-years of Texas and Washington.")
    a5 = T("A5")
    regression_block(sh, a5, cols_for(MAIN_FE), [
        ("Panel A. Ln damage", [three("Market share × Ln wildfire damage", "Market share", "Ln wildfire damage", {"panel": "A"})]),
        ("Panel B. Change in damage", [three("Market share × Δ Ln wildfire damage", "Market share", "Δ Ln wildfire damage", {"panel": "B"})])])
    sh.notes(MAIN_NOTE + STARS_NOTE)

    # ---- Appendix 6
    sh = Sheet(wb, "Appendix 6", 14, label_width=10, col_width=11)
    sh.title("Appendix 6", "Check of the wildfire damage data",
             "Panel A lists the ten largest state-year wildfire damages in Storm Events, 2013-2024, with the largest "
             "record behind each, what published sources report for that fire, and the fire-line loss ratio of the "
             "state market (all insurers) around the year. Panel B lists the state-years in which a fire's damage is "
             "repeated in its records for later months. Panel C repeats Table 2, Panel A, with the repeats counted "
             "once. Panel D turns the question round and lists the state-years where the market paid out heavily "
             "but Storm Events records little damage.")
    sh.panel_title("Panel A. Largest state-year wildfire damages")
    head = ["State", "Year", "Damage (USD m, 2024)", "Records with damage", "Largest record begins",
            "Largest record (USD m, 2024)", "Largest fire", "The fire began", "Published estimate",
            "Repeated (USD m)", "Corrected (USD m)", "Loss ratio t-1", "Loss ratio t", "Loss ratio t+1",
            "Loss ratio, median 2014-2023"]

    def num(v, fmt):
        return "" if pd.isna(v) else format(v, fmt)
    rows = [[r["state"], str(r["year"]), num(r["damage_usd_m"], ",.1f"), str(r["records_with_damage"]),
             r["largest_record_begins"], num(r["largest_record_usd_m"], ",.1f"), r["fire"], r["fire_began"],
             r["published_estimate"], num(r["repeated_usd_m"], ",.1f"), num(r["corrected_usd_m"], ",.1f"),
             num(r["loss_ratio_t_minus_1"], ".2f"), num(r["loss_ratio_t"], ".2f"),
             num(r["loss_ratio_t_plus_1"], ".2f"), num(r["loss_ratio_median_2014_2023"], ".2f")]
            for _, r in top.iterrows()]
    sh.ws.column_dimensions["G"].width = 26
    sh.ws.column_dimensions["H"].width = 16
    sh.ws.column_dimensions["I"].width = 60
    start = sh.r + 1
    simple_table(sh, head, rows, height=58)
    for rr in range(start, start + len(rows)):
        for cc in (7, 9):
            sh.ws.cell(row=rr, column=cc).alignment = WRAP
        sh.ws.row_dimensions[rr].height = 58
    sh.panel_title("Panel B. State-years with repeated records")
    simple_table(sh, ["State", "Year", "Repeated records", "Damage (USD m, 2024)", "Repeated (USD m)", "Corrected (USD m)"],
                 [[r["state_postal"], str(int(r["year"])), str(int(r["records"])), f"{r['damage_usd_m']:,.1f}",
                   f"{r['repeated_usd_m']:,.1f}", f"{r['corrected_usd_m']:,.1f}"] for _, r in changed.iterrows()])
    sh.ncols = 14
    sh.panel_title("Panel D. Years when the fire-line market paid out heavily and little damage is recorded")
    simple_table(sh, ["State", "Year", "Loss ratio", "Loss ratio, state median", "Damage (USD m, 2024)"],
                 [[r["state_postal"], str(int(r["year"])), f"{r['lr']:.2f}", f"{r['lr_median']:.2f}",
                   f"{r['damage_usd_m']:,.1f}"] for _, r in low.iterrows()])
    sh.ncols = 9
    sh.ws.column_dimensions["A"].width = 46
    sh.panel_title("Panel C. Table 2, Panel A, with the repeated records counted once")
    regression_block(sh, T("A6"), cols_for(MAIN_FE), [
        (None, [three("Market share × Ln wildfire damage (corrected)", "Market share", "Ln wildfire damage (corrected)", {"panel": "A"})])])
    sh.ncols = 14
    srcs = "; ".join(f"{r['fire']}: {r['source']}" for _, r in top.iterrows() if r["source"])
    wider = (corr["repeated_24_anyzone"].sum() - corr["repeated_24"].sum()) / 1e6
    sh.notes("Storm Events assigns each record to the year in which it begins, and no record in these twelve years "
             "runs from one year into the next. Damage is property plus crop damage in USD million of 2024. Loss "
             "ratio: losses incurred over premiums earned in the fire lines, all insurers writing in the state; the "
             "industry file has no group rows for 2024, so 2024 is left out. A record is counted as a repeat when it "
             "starts on the first day of a month and a record for the same forecast zone ending in the previous month "
             f"carries the same property (or crop) damage; Panel B lists all {len(changed)} state-years where this "
             "happens. The rule is a lower bound in one direction and imperfect in the other: allowing the fire's "
             f"record to move to another forecast zone would remove a further USD {wider:,.0f} million, almost all of "
             "it one Washington fire of 2024; and one record that the rule flags, a September 2014 California record "
             "of USD 0.6 million, belongs to a different fire whose damage happens to equal that of a fire recorded in "
             "August, so it is kept. Panel D is the opposite check: the largest recorded values can be compared with "
             "published figures, but damage missing from Storm Events altogether can only be seen indirectly, from "
             "what the state's insurers paid. " + (f"Sources: {srcs}. " if srcs else "") + STARS_NOTE)

    # ---- Figure 1
    sh = Sheet(wb, "Figure 1", 6, label_width=34, col_width=16)
    draws = int(f1["draws"].iloc[0])
    sh.title("Figure 1", "Placebo tests",
             f"Distribution of the coefficients (left) and t statistics (right) over {draws:,} placebo replications, "
             "for the number of violations in t+1 with insurer × state and state × year fixed effects. Rows: damage "
             "reshuffled across states within each year; exposure reshuffled between insurers in the same state and "
             "year; each state's damage series reshuffled across years; the last two combined. Dots: placebo draws; "
             "line: fitted normal; dashed line: actual estimate.")
    add_image(sh, out / "figure1_placebo.png", 820)
    sh.panel_title("Permutation p-values")
    simple_table(sh, ["Scheme", "Draws", "Actual t", "95th percentile of |t|", "p"],
                 [[SCHEME_LABEL[r["scheme"]], f"{int(r['draws']):,}", f"{r['t_actual']:.2f}", f"{r['t_q95']:.2f}",
                   f"{r['p_two_sided']:.3f}"] for _, r in f1.iterrows()])
    sh.panel_title(f"Damage reshuffled across states: all three outcomes ({int(t10['draws'].iloc[0]):,} draws)")
    simple_table(sh, ["Outcome", "Fixed effects", "t", "95th percentile of |t|", "p = r/B", "p = (1+r)/(B+1)"],
                 [[DV_NAME[r["y"].replace("_f1", "")], FE_LABEL[r["fe"]], f"{r['t']:.2f}", f"{r['t_95']:.2f}",
                   f"{r['p']:.3f}", f"{r['p_rb']:.3f}"] for _, r in t10.sort_values(["fe", "y"]).iterrows()])
    sh.notes("r is the number of draws with |t| at least as large as the actual one and B the number of draws; "
             "(1 + r) / (B + 1) counts the actual draw.")

    # ---- Figure 2
    sh = Sheet(wb, "Figure 2", 8, label_width=34, col_width=12)
    sh.title("Figure 2", "Dynamic effect",
             "Coefficients on Exposure × Ln wildfire damage(t+k), k = -3 ... +3, all entered in the same regression, "
             "with 95% confidence intervals; the outcome is measured in t+1, so k >= +2 is damage arriving after the "
             "violations (shaded). Insurer × state and state × year fixed effects. Left: exposure in year t; right: "
             "first-year exposure. Rows: number of violations, ln(1 + penalties), any violation.")
    add_image(sh, out / "figure2_dynamic.png", 820)
    sh.panel_title("Figure 2 (continued). Window of plus or minus two years")
    add_image(sh, out / "figure2_dynamic_w2.png", 820)
    for win in [3, 2]:
        for des, dname in [("current", "exposure in year t"), ("base", "first-year exposure")]:
            g = f2[(f2["window"] == win) & (f2["design"] == des)]
            gk = g[g["k"].notna()]
            ks = sorted(gk["k"].astype(int).unique())
            sh.panel_title(f"Coefficients plotted: ±{win} years, {dname} ({gk['years'].iloc[0]})")
            rows = []
            for dv in FIG_DVS:
                gg = gk[gk["dv"] == dv].sort_values("k")
                e = g[(g["dv"] == dv) & (g["term"] == "expo")].iloc[0]
                ecell = "absorbed" if e.get("absorbed") == 1 else f"{e['b']:.4f}{star(e['p'])}"
                rows.append([f"{DV_NAME[dv.replace('_f1', '')]} (N = {int(gg['N'].iloc[0]):,})"]
                            + [f"{r['b']:.4f}{star(r['p'])}" for _, r in gg.iterrows()] + [ecell])
                rows.append([""] + [f"({r['se']:.4f})" for _, r in gg.iterrows()]
                            + ["" if e.get("absorbed") == 1 else f"({e['se']:.4f})"])
            simple_table(sh, ["k"] + [f"{k:+d}" for k in ks] + ["Exposure"], rows)
    sh.panel_title("Checks on the k = +2 term (±3 years, exposure in year t)")
    rows = []
    for c in ant["check"].drop_duplicates():
        cc = [ant[(ant["check"] == c) & (ant["dv"] == dv) & (ant["k"] == 2)].iloc[0] for dv in FIG_DVS]
        rows.append([c] + [f"{x['b']:.4f}{star(x['p'])}" for x in cc])
        rows.append([""] + [f"({x['se']:.4f})" for x in cc])
    simple_table(sh, ["", DV_NAME["count"], DV_NAME["lnpen"], DV_NAME["any"]], rows)
    sh.panel_title(f"Change in damage: Exposure × Δ Ln wildfire damage(t+k) in one regression ({f2c['years'].iloc[0]})")
    rows = []
    ks = sorted(f2c.loc[f2c["k"].notna(), "k"].astype(int).unique())
    for smp in ["full", "without TX and WA"]:
        for dv in FIG_DVS:
            sub = f2c[(f2c["sample"] == smp) & (f2c["dv"] == dv)]
            gg = sub[sub["k"].notna()].sort_values("k")
            e = sub[sub["term"] == "expo"].iloc[0]
            lab = DV_NAME[dv.replace("_f1", "")] + (", without TX and WA" if smp != "full" else "")
            rows.append([lab] + [f"{r['b']:.4f}{star(r['p'])}" for _, r in gg.iterrows()]
                        + [f"{e['b']:.4f}{star(e['p'])}"])
            rows.append([""] + [f"({r['se']:.4f})" for _, r in gg.iterrows()] + [f"({e['se']:.4f})"])
    simple_table(sh, ["k"] + [f"{k:+d}" for k in ks] + ["Exposure"], rows)
    sh.notes("Exposure is the market share in the fire lines; with first-year exposure, insurer × state effects absorb "
             "it. The damage terms vary by state and year and are absorbed by state × year effects. The checks: "
             "baseline; adding violations in t and t-1; dropping Texas and Washington (all with insurer × state and "
             "state × year effects); and, because state × year effects span state-specific trends, a pair with insurer "
             "× state and insurer × year effects and the damage terms themselves, without and with state-specific "
             "linear trends - compare those two rows with each other. " + STARS_NOTE)

    # ---- Figure 3
    sh = Sheet(wb, "Figure 3", 8, label_width=44, col_width=12)
    sh.title("Figure 3", "Checks on the dynamic effect",
             f"Figure 2 re-estimated with controls for size and market concentration, under {len(FE)} sets of fixed "
             "effects, with damage dummies in place of ln damage, and with wildfire damage corrected for repeated "
             "records. The figure shows the t statistics of the terms at k = 0 (damage in t) and k = +2 (damage in t+2, "
             "after the violations) for the window of plus or minus three years and exposure in year t. The tables give "
             "the coefficients and count, over the twelve panels of Figure 2 (three outcomes, two exposure measures, two "
             "windows), how often each term is significant at 5%.")
    add_image(sh, out / "figure3_checks.png", 900)
    sh.panel_title("Figure 3 (continued). Figure 2 with the repeated records counted once")
    add_image(sh, out / "figure3_corrected_damage.png", 820)

    def check_rows(check):
        g = chk[chk["check"] == check]
        rows = []
        for v in g["variant"].drop_duplicates():
            gv = g[g["variant"] == v]
            main = gv[(gv["window"] == 3) & (gv["design"] == "current")]
            cells = []
            ses = []
            for dv in FIG_DVS:
                for k in (0, 2):
                    x = main[(main["dv"] == dv) & (main["k"] == k)].iloc[0]
                    cells.append(f"{x['b']:.4f}{star(x['p'])}")
                    ses.append(f"({x['se']:.4f})")
            n = int(main["N"].iloc[0])
            s0 = int((gv[gv["k"] == 0]["p"] < 0.05).sum())
            s2 = int((gv[gv["k"] == 2]["p"] < 0.05).sum())
            rows.append([v] + cells + [f"{n:,}", f"{s0} / 12", f"{s2} / 12"])
            rows.append([""] + ses + ["", "", ""])
        return rows

    head = ["", "Violations, k = 0", "Violations, k = +2", "Ln penalties, k = 0", "Ln penalties, k = +2",
            "Any violation, k = 0", "Any violation, k = +2", "N", "k = 0 significant", "k = +2 significant"]
    sh.ncols = 9
    for c in range(2, 12):
        sh.ws.column_dimensions[get_column_letter(c)].width = 13
    for title, check in [("Panel A. Controls (insurer × state and state × year effects)", "controls"),
                         ("Panel B. Fixed effects, no controls", "fixed effects"),
                         ("Panel C. Fixed effects, controls for ln insurer size and the HHI", "fixed effects, controls"),
                         ("Panel D. Damage dummies (insurer × state and state × year effects)", "damage dummies"),
                         ("Panel E. Wildfire damage with repeated records counted once", "corrected damage")]:
        sh.panel_title(title)
        simple_table(sh, head, check_rows(check), height=44)
    sh.notes("Each row is the regression of Figure 2 (window of plus or minus three years, exposure in year t); the "
             "two columns on the right count significant terms over all twelve panels. Exposure and the damage terms "
             "enter on their own wherever the fixed effects do not absorb them; first-year exposure is absorbed by "
             "insurer × state effects and the damage terms and the HHI by state × year effects, and all estimates are "
             "in output/figure3_checks.csv. Size is ln premiums written by the insurer in all lines in the state; "
             "concentration is the HHI of fire-line premiums in the state and year; the last row of Panel A adds "
             "market share × HHI, which state × year effects do not absorb. Damage dummies: the median of wildfire "
             f"damage over state-years is zero, so the above-median dummy is the any-damage dummy; the 75th and 90th "
             f"percentiles are about USD {fire['p75_usd_million']:.2f} million and USD {fire['p90_usd_million']:.1f} "
             "million. Panel E uses the corrected damage of Appendix 6. " + STARS_NOTE)
    return wb


# ----------------------------------------------------------------------------- main

def main():
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=here / "1_data")
    ap.add_argument("--out", type=Path, default=here / "output")
    ap.add_argument("--draws", type=int, default=1000, help="placebo draws for Figure 1")
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    print("reading the data")
    master = read_master(args.data)
    market = market_structure(master)
    fires = pd.read_csv(args.data / "storm_events_wildfire_2013_2024.csv", dtype=str, keep_default_na=False)
    reg_raw = pd.read_csv(args.data / "rstreet_scores_2024.csv")
    check_rstreet(reg_raw)
    panel, notes, splits, corr, rep = build_panel(args.data, master, market, fires)
    print(f"{len(panel):,} insurer-state-years; samples {int(panel['in_sample'].sum()):,} (portfolio share) "
          f"and {int(panel['in_sample_ms'].sum()):,} (market share)")
    defs = definitions(notes, reg_raw)
    summ = summary(panel, {k: v[0] for k, v in defs.items()})

    print("tables")
    res = pd.DataFrame([row for s in specifications() for row in run(panel, s)])
    print(f"  {res.drop_duplicates(SPEC_KEYS).shape[0]:,} regressions")
    power_tab, dummy_mde, cells = power(res, panel)
    mx = market_exit(market, panel)

    print("figures 2 and 3")
    f2 = figure2(panel, out)
    ant = pd.concat([anticipation_checks(panel, dv) for dv in FIG_DVS], ignore_index=True)
    f2c = change_dynamic(panel)
    chk = figure2_checks(panel)
    draw_checks(chk, out / "figure3_checks.png")
    draw_dynamic(chk[chk["check"] == "corrected damage"], 3, out / "figure3_corrected_damage.png",
                 "Figure 3 (continued)  Dynamic effect with repeated wildfire records counted once, window of plus or minus 3 years")
    sources = pd.read_csv(args.data / "damage_check_sources.csv", dtype=str, keep_default_na=False)
    top, changed, low = damage_check(panel, corr, rep, fires, master, sources)

    if args.draws > 0:
        print("figure 1")
        f1 = figure1(panel, out, args.draws)
        t10 = placebo_cells(panel, args.draws)
        f1.to_csv(out / "figure1_permutation.csv", index=False)
        t10.to_csv(out / "figure1_placebo_cells.csv", index=False)
    else:
        f1 = pd.read_csv(out / "figure1_permutation.csv")
        t10 = pd.read_csv(out / "figure1_placebo_cells.csv")
    draw_figure1(out, f1)

    print("appendix 7")
    panel = add_appendix7(panel, fires, master)
    dating = record_dating(fires)
    pers = damage_persistence(panel)
    a7 = appendix7_checks(panel)
    draw_appendix7(a7, out / "figure4_appendix7.png")

    res.to_csv(out / "regressions.csv", index=False)
    summ.to_csv(out / "summary.csv", index=False)
    notes.to_csv(out / "damage_notes.csv", index=False)
    splits.to_csv(out / "regulation_splits.csv", index=False)
    power_tab.to_csv(out / "power.csv", index=False)
    dummy_mde.to_csv(out / "dummy_change_mde.csv", index=False)
    cells.to_csv(out / "identifying_cells.csv", index=False)
    mx.to_csv(out / "market_exit_check.csv", index=False)
    f2.to_csv(out / "figure2_dynamic.csv", index=False)
    ant.to_csv(out / "anticipation_checks.csv", index=False)
    f2c.to_csv(out / "figure2_dynamic_change.csv", index=False)
    chk.to_csv(out / "figure3_checks.csv", index=False)
    top.to_csv(out / "damage_check.csv", index=False)
    low.to_csv(out / "damage_not_recorded.csv", index=False)
    rep.to_csv(out / "repeated_wildfire_records.csv", index=False)
    corr.to_csv(out / "wildfire_damage_corrected.csv", index=False)
    dating.to_csv(out / "appendix7_record_dating.csv", index=False)
    pers.to_csv(out / "appendix7_persistence.csv", index=False)
    a7.to_csv(out / "appendix7_checks.csv", index=False)

    wb = workbook(out, res, summ, notes, splits, defs, power_tab, cells, f1, t10, f2, ant, f2c, chk, mx,
                  top, changed, low, corr)
    appendix7_sheet(wb, dating, pers, a7, out)
    wb.save(out / "tables_and_figures.xlsx")
    print(f"done: {out / 'tables_and_figures.xlsx'}")


# ----------------------------------------------------------------------------- appendix 7

A7_DAMAGE = [(DAMAGE, "Ln wildfire damage (Figure 2)"),
             (f"{DAMAGE}_h2", "Fires beginning July to December"),
             (f"{DAMAGE}_h1", "Fires beginning January to June"),
             (f"{DAMAGE}_shift", "November and December fires dated to the year after"),
             ("lrshock_fire4", "Fire-line loss ratio of the state market")]
A7_RISK = "Exposure × the state's average damage"
A7_MEASURE_NAME = {"lndmg_fire": "Ln wildfire damage", "dmgany_fire": "Any damage"}


def record_dating(fires):
    """Where in the year the wildfire damage falls. Storm Events dates a record by the month in
    which the fire begins, so a fire burning across a year end would carry its damage in the
    earlier year; no record does. What the dating cannot separate is the year in which a fire is
    paid and complained about, and most of the damage arrives too late in the year for that to
    be the year of the fire itself."""
    f = fires.copy()
    f["dmg"] = f["DAMAGE_PROPERTY"].map(dollars) + f["DAMAGE_CROPS"].map(dollars)
    begin, end = f["BEGIN_YEARMONTH"].astype(int), f["END_YEARMONTH"].astype(int)
    month = begin % 100
    total = f["dmg"].sum()
    rows = [{"item": "Wildfire records, 2013-2024", "value": float(len(f)), "share": False, "note": ""},
            {"item": "Records carrying damage", "value": float((f["dmg"] > 0).sum()), "share": False, "note": ""},
            {"item": "Records whose end year differs from the begin year", "share": False,
             "value": float((begin // 100 != end // 100).sum()),
             "note": "a record dated to the wrong year would have to be one of these"}]
    for lo, hi, label in [(1, 6, "January to June"), (7, 10, "July to October"),
                          (11, 12, "November and December")]:
        sel = month.between(lo, hi)
        rows.append({"item": f"Share of the damage, fires beginning {label}", "share": True,
                     "value": float(f.loc[sel, "dmg"].sum() / total),
                     "note": f"{int((f.loc[sel, 'dmg'] > 0).sum())} records carry damage"})
    big = f.nlargest(2, "dmg")
    rows.append({"item": "Share of the damage, the two largest records", "share": True,
                 "value": float(big["dmg"].sum() / total),
                 "note": "; ".join(f"{r['STATE'].title()} {r['BEGIN_DATE_TIME'][:9]}" for _, r in big.iterrows())})
    return pd.DataFrame(rows)


def timing_series(panel, fires, cpi):
    """Wildfire damage split by the half of the year in which the fire begins, and the series
    that moves a fire beginning in November or December into the following year. Deflated and
    logged like the damage in the panel."""
    f = fires.copy()
    f["dmg"] = f["DAMAGE_PROPERTY"].map(dollars) + f["DAMAGE_CROPS"].map(dollars)
    names = panel.drop_duplicates("state_postal").set_index("state_name_uc")["state_postal"]
    f["state_postal"] = f["STATE"].map(names)
    f = f.dropna(subset=["state_postal"])
    month = f["BEGIN_YEARMONTH"].astype(int) % 100
    f["year"] = f["YEAR"].astype(int)
    f["year_shift"] = f["year"] + (month >= 11).astype(int)
    out = panel.drop_duplicates(["state_postal", "year"])[["state_postal", "year"]].copy()
    for stem, sel in [("h1", month <= 6), ("h2", month >= 7)]:
        g = f[sel].groupby(["state_postal", "year"], as_index=False)["dmg"].sum()
        out = out.merge(g.rename(columns={"dmg": f"nom_{stem}"}), on=["state_postal", "year"], how="left")
    g = f.groupby(["state_postal", "year_shift"], as_index=False)["dmg"].sum()
    out = out.merge(g.rename(columns={"year_shift": "year", "dmg": "nom_shift"}),
                    on=["state_postal", "year"], how="left")
    factor = out["year"].map(cpi[2024] / cpi)
    for stem in ["h1", "h2", "shift"]:
        # a state-year with no fire in that half of the year has no row to merge, and no fire is no damage
        out[f"{DAMAGE}_{stem}"] = np.log1p(out[f"nom_{stem}"].fillna(0.0) * factor / 1e6)
    return out[["state_postal", "year"] + [f"{DAMAGE}_{s}" for s in ["h1", "h2", "shift"]]]


def loss_ratio_shock(master):
    """A shock built from what the fire-line market paid rather than from the recorded damage:
    the state's fire-line loss ratio in the year, less its own loss ratio over the whole period.
    Appendix 6 shows the damage field understates the fires that destroyed houses and carries
    the federal cost of fighting remote timber fires, so the loss ratio is the closer measure of
    a bad fire year. The industry file has market rows for 2014-2023 only."""
    fl = master[(master["territory"] == 0) & (master["lg_fire4"] == 1)]
    agg = (fl.assign(pe=fl["premiums_earned"].fillna(0).clip(lower=0), li=fl["losses_incurred"].fillna(0))
           .groupby(["state_postal", "year"], as_index=False)[["pe", "li"]].sum())
    agg = agg[(agg["pe"] > 0) & agg["year"].between(FIRST_YEAR, LAST_MS_YEAR)].copy()
    # states differ in the level of their loss ratios; centring on each state's own ratio makes the
    # shock comparable across them, and insurer x state effects absorb what the centring takes out
    whole = agg.groupby("state_postal")[["pe", "li"]].transform("sum")
    agg["lrshock_fire4"] = agg["li"] / agg["pe"] - whole["li"] / whole["pe"]
    return agg[["state_postal", "year", "lrshock_fire4"]]


def add_appendix7(panel, fires, master):
    """The alternative damage series of Appendix 7, and the state's average damage over the
    sample. State x year effects absorb that average, but not its product with the insurer's
    exposure, which is what every term of Figure 2 is built from: if the terms after the
    violations only pick up exposed insurers sitting in fire-prone states, the product takes
    them out."""
    cpi = master.groupby("year")["CPI"].first()
    n0 = len(panel)
    panel = panel.merge(timing_series(panel, fires, cpi), on=["state_postal", "year"], how="left")
    panel = panel.merge(loss_ratio_shock(master), on=["state_postal", "year"], how="left")
    sy = panel[panel["year"].between(FIRST_YEAR, LAST_MS_YEAR)].drop_duplicates(["state_postal", "year"])
    panel = panel.merge(sy.groupby("state_postal")[DAMAGE].mean().rename("risk_fire").reset_index(),
                        on="state_postal", how="left")
    panel["x_risk"] = panel[EXPO] * panel["risk_fire"]
    panel["x_risk_base"] = panel[f"{EXPO}_base"] * panel["risk_fire"]
    if len(panel) != n0:
        raise ValueError(f"a merge changed the number of rows: {n0} -> {len(panel)}")
    return panel


def damage_persistence(panel, window=3):
    """Whether the terms after the violations carry anything of their own. Wildfire damage is
    persistent within a state, so a term at k = +2 can load on the damage at k = 0 through that
    persistence alone. Reports the autocorrelation of the state-year series, how far each term
    of Figure 2 is correlated with the term at k = 0, and each term estimated on its own on the
    sample of the joint regression, so that the two are comparable."""
    sy = panel.drop_duplicates(["state_postal", "year"])[["state_postal", "year", DAMAGE, "dmgany_fire"]]
    sy = sy.sort_values(["state_postal", "year"])
    rows = []
    for col in [DAMAGE, "dmgany_fire"]:
        for lag in range(1, window + 1):
            s = sy.assign(other=sy.groupby("state_postal")[col].shift(lag))
            s = s[s[[col, "other"]].notna().all(axis=1)]
            rows.append({"part": "autocorrelation of the state-year series", "measure": col, "k": lag,
                         "b": float(s[col].corr(s["other"])), "se": np.nan, "p": np.nan, "N": len(s)})
    df = with_leads(panel, DAMAGE, window)
    ks = list(range(-window, window + 1))
    xs = [tag(k) for k in ks]
    for k in ks:
        df[tag(k)] = df[EXPO] * df[f"dmg_{tag(k)}"]
    for dv in FIG_DVS:
        # every fit below uses the sample of the joint regression, so the two columns compare like with like
        d = df[df[[dv, EXPO] + xs].notna().all(axis=1)]
        for k in ks:
            rows.append({"part": "correlation with the term at k = 0", "measure": dv, "k": k,
                         "b": float(d[tag(k)].corr(d[tag(0)])), "se": np.nan, "p": np.nan, "N": len(d)})
        joint = feols(d, dv, xs + [EXPO], FE["F7"])
        b, se, p = joint.coef(), joint.se(), joint.pvalue()
        for k in ks:
            rows.append({"part": "all terms in one regression", "measure": dv, "k": k, "b": float(b[tag(k)]),
                         "se": float(se[tag(k)]), "p": float(p[tag(k)]), "N": joint._N})
        for k in ks:
            m = feols(d, dv, [tag(k), EXPO], FE["F7"])
            b1, se1, p1 = m.coef(), m.se(), m.pvalue()
            rows.append({"part": "one term at a time", "measure": dv, "k": k, "b": float(b1[tag(k)]),
                         "se": float(se1[tag(k)]), "p": float(p1[tag(k)]), "N": m._N})
    return pd.DataFrame(rows)


def appendix7_checks(panel):
    """Figure 2 again with each alternative damage series, and with exposure x the state's
    average damage added to the series of Figure 2 itself."""
    out = []
    for damage, label in A7_DAMAGE:
        for w in (3, 2):
            for d in ("current", "base"):
                for dv in FIG_DVS:
                    out.append(dynamic(panel, dv, w, d, "F7", damage)
                               .assign(check="damage measure", variant=label, damage=damage, controls="none"))
    for w in (3, 2):
        for d in ("current", "base"):
            # the control has to be built on the same exposure the design uses, or it absorbs the wrong thing
            ctrl = ("x_risk",) if d == "current" else ("x_risk_base",)
            for dv in FIG_DVS:
                out.append(dynamic(panel, dv, w, d, "F7", DAMAGE, ctrl)
                           .assign(check="long-run risk", variant=A7_RISK, damage=DAMAGE, controls=ctrl[0]))
    return pd.concat(out, ignore_index=True)


def draw_appendix7(chk, path):
    """t statistics of the k = 0 and k = +2 terms across the checks of Appendix 7."""
    g = chk[(chk["window"] == 3) & (chk["design"] == "current") & chk["k"].isin([0, 2])].copy()
    g["t"] = g["b"] / g["se"]
    items = []
    for check, title in [("damage measure", "Damage measure"), ("long-run risk", "Added control")]:
        items.append((title, None))
        items += [(v, check) for v in g.loc[g["check"] == check, "variant"].drop_duplicates()]
    ypos = np.arange(len(items))[::-1]
    fig, axes = plt.subplots(1, 3, figsize=(13, 0.4 * len(items) + 2.0), sharey=True)
    for j, dv in enumerate(FIG_DVS):
        ax = axes[j]
        for k, color, mk in [(0, BLUE, "o"), (2, RED, "s")]:
            pts = [(y, g[(g["check"] == c) & (g["variant"] == v) & (g["dv"] == dv) & (g["k"] == k)]["t"].iloc[0])
                   for y, (v, c) in zip(ypos, items) if c is not None]
            ax.scatter([t for _, t in pts], [y for y, _ in pts], color=color, marker=mk, s=22, zorder=3,
                       label="k = 0 (damage in t)" if k == 0 else "k = +2 (damage in t+2)")
        for x in (-1.96, 1.96):
            ax.axvline(x, color="0.6", lw=0.8, ls="--")
        ax.axvline(0, color="0.35", lw=0.8)
        for y, (_, c) in zip(ypos, items):
            if c is None:
                ax.axhline(y, color="0.92", lw=7, zorder=0)
        ax.set_title(DV_LABEL[dv], fontsize=10)
        ax.set_xlabel("t statistic")
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_yticks(ypos)
    axes[0].set_yticklabels([lab for lab, _ in items], fontsize=8)
    for tick, (_, c) in zip(axes[0].get_yticklabels(), items):
        if c is None:
            tick.set_fontweight("bold")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.885))
    fig.suptitle("Figure 4  Where the term at k = +2 of Figure 2 comes from: t statistics at k = 0 and k = +2\n"
                 "window of plus or minus 3 years, exposure in year t; dashed lines at ±1.96", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.84))
    fig.savefig(path, dpi=200)
    plt.close(fig)


def a7_rows(chk, check):
    """The k = 0 and k = +2 terms of each variant, beside how often each is significant over the
    twelve panels of Figure 2."""
    g = chk[chk["check"] == check]
    rows = []
    for v in g["variant"].drop_duplicates():
        sub = g[g["variant"] == v]
        main = sub[(sub["window"] == 3) & (sub["design"] == "current") & (sub["dv"] == "count_f1")]
        cells = []
        for k in (0, 2):
            r = main[main["k"] == k].iloc[0]
            s = sub[sub["k"] == k]
            cells += [f"{r['b']:.4f}{star(r['p'])} ({r['se']:.4f})", f"{int((s['p'] < 0.05).sum())} / {len(s)}"]
        rows.append([v] + cells + [f"{int(main['N'].iloc[0]):,}"])
    return rows


def appendix7_sheet(wb, dating, pers, chk, out):
    """Appendix 7: what the term at k = +2 of Figure 2 is made of."""
    sh = Sheet(wb, "Appendix 7", 5, label_width=48, col_width=18)
    sh.title("Appendix 7", "Where the term at k = +2 of Figure 2 comes from",
             "Three things could put a coefficient on damage that arrives after the violations: the dating "
             "of the damage, the persistence of wildfire damage within a state, and the sorting of exposed "
             "insurers into fire-prone states. The panels take them in that order.")

    sh.panel_title("Panel A. The dating of the damage")
    simple_table(sh, ["NOAA Storm Events wildfire records", "Value"],
                 [[r["item"], f"{r['value']:.3f}" if r["share"] else f"{int(r['value']):,}"]
                  for _, r in dating.iterrows()], height=16)

    sh.panel_title("Panel B. The persistence of wildfire damage")
    ac = pers[pers["part"] == "autocorrelation of the state-year series"]
    simple_table(sh, ["Autocorrelation within the state", "Lag 1", "Lag 2", "Lag 3"],
                 [[A7_MEASURE_NAME[m]] + [f"{ac[(ac['measure'] == m) & (ac['k'] == k)]['b'].iloc[0]:.3f}"
                                          for k in (1, 2, 3)]
                  for m in ac["measure"].drop_duplicates()], height=16)
    cor, one, joint = (pers[pers["part"] == part] for part in
                       ("correlation with the term at k = 0", "one term at a time", "all terms in one regression"))

    def cell(t, k, fmt):
        r = t[(t["measure"] == "count_f1") & (t["k"] == k)].iloc[0]
        return f"{r['b']:{fmt}}" + (star(r["p"]) if pd.notna(r["p"]) else "")

    simple_table(sh, ["Exposure × damage(t+k), number of violations", "Correlation with k = 0",
                      "Estimated on its own", "In one regression"],
                 [[f"k = {k:+d}" if k else "k = 0", cell(cor, k, ".3f"), cell(one, k, ".4f"),
                   cell(joint, k, ".4f")] for k in range(-3, 4)], height=16)

    head = ["", "k = 0", "k = 0 significant", "k = +2", "k = +2 significant", "N"]
    sh.panel_title("Panel C. The damage measure")
    simple_table(sh, head, a7_rows(chk, "damage measure"), height=30)
    sh.panel_title("Panel D. Exposure × the state's average damage, added to Figure 2")
    simple_table(sh, head, a7_rows(chk, "long-run risk"), height=30)

    add_image(sh, out / "figure4_appendix7.png", 940)
    sh.notes("Panel A counts the wildfire records of NOAA Storm Events, each dated by the month in which "
             "the fire begins; a record whose fire ran across a year end would carry its damage in the "
             "earlier year, and none does, so the shares below it are what the annual dating leaves open. "
             "Panel B takes the damage of Figure 2: the first column is the correlation of "
             "exposure × damage(t+k) with exposure × damage(t) over the sample of Figure 2, the second "
             "estimates that term alone on the same sample, and the third repeats the joint estimate of "
             "Figure 2. Panels C and D re-estimate Figure 2 under insurer × state and state × year effects; "
             "the coefficient columns are the number of violations with a window of plus or minus three "
             "years and exposure in year t, and the counts are over all twelve panels of Figure 2 at the 5% "
             "level. The two half-year series split the damage of a state and year by the month in which "
             "the fire begins, so they sum to the damage of Figure 2; the loss-ratio shock is observed for "
             "2014-2023 only and its window is correspondingly shorter. Every estimate is in "
             "output/appendix7_checks.csv. " + STARS_NOTE)
    # the sheet is added after the workbook is built, so move the tab to where the contents say
    wb.move_sheet(sh.ws, offset=wb.sheetnames.index("Appendix 6") + 1 - wb.sheetnames.index("Appendix 7"))
    return wb

if __name__ == "__main__":
    main()
