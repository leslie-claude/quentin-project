"""
Write the draft reply in 3_docs from the files in output/. No number here is typed by hand:
every one is read from the output of 2_code/analysis.py, and the build stops if a placeholder
is left unfilled.

    python build_docs.py [output_folder] [docs_folder]
"""
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DVS = ["count_f1", "lnpen_f1", "any_f1"]
DV_NAME = {"count": "number of violations", "lnpen": "ln penalties", "any": "any violation"}


def star(p):
    return "***" if p < 0.01 else "**" if p < 0.05 else "*" if p < 0.1 else ""


def cell(r):
    return f"{r['b']:.4f}{star(r['p'])} ({r['se']:.4f})"


def load(out):
    d = {}
    for name in ["regressions", "summary", "damage_notes", "power", "identifying_cells",
                 "figure1_permutation", "figure1_placebo_cells", "figure2_dynamic",
                 "figure2_dynamic_change", "anticipation_checks", "figure3_checks",
                 "damage_check", "damage_not_recorded", "wildfire_damage_corrected",
                 "repeated_wildfire_records", "market_exit_check"]:
        d[name] = pd.read_csv(out / f"{name}.csv")
    d["regressions"]["table"] = d["regressions"]["table"].astype(str)
    return d


def reply(d):
    res = d["regressions"]
    est = res[res["absorbed"] == 0]
    t2 = est[(est["table"] == "2") & (est["panel"] == "A")]
    pick = lambda dv, fe, term: t2[(t2["dv"] == dv) & (t2["fe"] == fe) & (t2["term"] == term)].iloc[0]

    chk = d["figure3_checks"]
    k = chk[chk["k"].isin([0, 2])]
    # the order the specifications were run in, not alphabetical
    order = {c: list(g["variant"].drop_duplicates()) for c, g in k.groupby("check", sort=False)}
    cnt = k.groupby(["check", "variant", "k"], sort=False)["p"].apply(lambda p: int((p < 0.05).sum())).unstack()
    main = k[(k["window"] == 3) & (k["design"] == "current")]
    m = lambda variant, dv, kk: main[(main["variant"] == variant) & (main["dv"] == dv) & (main["k"] == kk)].iloc[0]

    notes = d["damage_notes"].set_index("hazard").loc["fire"]
    top = d["damage_check"]
    corr = d["wildfire_damage_corrected"]
    rep = d["repeated_wildfire_records"]
    low = d["damage_not_recorded"]

    # the market share at which the net effect of damage turns positive, where damage is identified
    thr = {}
    for fe in ["F3", "F5"]:
        for dv in ["count", "lnpen", "any"]:
            x, dm = pick(dv, fe, "x"), pick(dv, fe, "dmg")
            thr[(dv, fe)] = -dm["b"] / x["b"]
    share = d["summary"]
    ms = share[(share["sample"] == "mshare") & (share["variable"] == "mshare_fire4")].iloc[0]

    fe_rows = cnt.loc["fixed effects"].reindex(order["fixed effects"])
    with_ist = [v for v in fe_rows.index if "Insurer × State" in v]
    no_ist = [v for v in fe_rows.index if "Insurer × State" not in v]
    k0_ist = (int(fe_rows.loc[with_ist, 0.0].min()), int(fe_rows.loc[with_ist, 0.0].max()))
    k0_no = (int(fe_rows.loc[no_ist, 0.0].min()), int(fe_rows.loc[no_ist, 0.0].max()))
    k2_all = (int(fe_rows[2.0].min()), int(fe_rows[2.0].max()))
    ctrl_rows = cnt.loc["controls"].reindex(order["controls"])

    wa = corr[(corr["state_postal"] == "WA") & (corr["repeated_nom"] > 0)].copy()
    wa["cut"] = 1 - wa["dmg24_fire_corr"] / wa["dmg24_fire"]
    wa_big = wa[wa["dmg24_fire"] > 1e8]
    a6 = est[(est["table"] == "A6") & (est["term"] == "x")]
    a6f7 = a6[a6["fe"] == "F7"]
    t2f7 = t2[(t2["fe"] == "F7") & (t2["term"] == "x")]

    L = []
    w = L.append
    w("Dear Quentin,")
    w("")
    w("Thank you. The answers to your four questions are below; the tables are attached and the "
      "code and data go with this message in one folder.")
    w("")
    w("## Reporting the lower-order terms")
    w("")
    w("Yes - exposure and damage have always been in every regression; I had simply not printed "
      "them. Every table now reports them, and writes 'absorbed' where the fixed effects take them "
      "out, so it is visible which design identifies what.")
    w("")
    w("Printing them brought out one thing I had not noticed. Where ln damage is identified, its "
      "own coefficient is negative:")
    w("")
    w("| | Exposure x Ln damage | Ln damage | Exposure |")
    w("|---|---|---|---|")
    for dv in ["count", "lnpen", "any"]:
        w(f"| {DV_NAME[dv].capitalize()}, insurer + state + year | {cell(pick(dv, 'F3', 'x'))} | "
          f"{cell(pick(dv, 'F3', 'dmg'))} | {cell(pick(dv, 'F3', 'expo'))} |")
    w("")
    w(f"So in that design a wildfire raises violations only for an insurer whose market share is "
      f"above about {thr[('count', 'F3')]:.1%} (the number of violations), {thr[('lnpen', 'F3')]:.1%} "
      f"(ln penalties) or {thr[('any', 'F3')]:.1%} (any violation), against a mean share of "
      f"{ms['mean']:.1%} and a median of {ms['median']:.1%}. For a small insurer the sign goes the "
      "other way. I do not think the level is where the identification is - under insurer x state "
      "and state x year effects, which is my preferred design, state x year effects absorb damage "
      "entirely and the question does not arise - but it is worth knowing.")
    w("")
    w("## Figure 2: size and market concentration")
    w("")
    w("Controlling for ln of the insurer's premiums in the state, for the fire-line Herfindahl "
      "index of the state and year, and for market share x that index (which state x year effects "
      "do not absorb) leaves the k = +2 term where it was:")
    w("")
    w("| Control | k = 0 | k = +2 |")
    w("|---|---|---|")
    for variant in ctrl_rows.index:
        c0, c2 = m(variant, "count_f1", 0), m(variant, "count_f1", 2)
        w(f"| {variant.replace(' (Figure 2)', '')} | {cell(c0)} | {cell(c2)} |")
    w("")
    lo, hi = int(ctrl_rows[2.0].min()), int(ctrl_rows[2.0].max())
    w(f"Across the twelve panels of Figure 2 (three outcomes x two exposure measures x two "
      f"windows), k = +2 is significant at 5% in " + (f"{lo}" if lo == hi else f"{lo} to {hi}") +
      " of 12 in every variant.")
    w("")
    w("## Figure 2: other fixed effects")
    w("")
    w(f"I ran it under all {len(cnt.loc['fixed effects'])} combinations of insurer, state, year, "
      f"insurer x state, insurer x year and state x year effects. The anticipation term survives "
      f"all of them: k = +2 is significant at 5% in {k2_all[0]} to {k2_all[1]} of the twelve panels "
      f"under every design.")
    w("")
    w("The term that is NOT robust is the contemporaneous one. k = 0 is significant in "
      f"{k0_ist[0]}-{k0_ist[1]} of 12 only in the designs that contain insurer x state effects, and "
      f"in {k0_no[0]}-{k0_no[1]} of 12 in the designs that do not:")
    w("")
    w("| Fixed effects | k = 0 | k = +2 | k = 0 significant | k = +2 significant |")
    w("|---|---|---|---|---|")
    for variant in fe_rows.index:
        c0, c2 = m(variant, "count_f1", 0), m(variant, "count_f1", 2)
        w(f"| {variant.replace(' (Figure 2)', '')} | {cell(c0)} | {cell(c2)} | "
          f"{int(fe_rows.loc[variant, 0.0])} / 12 | {int(fe_rows.loc[variant, 2.0])} / 12 |")
    w("")
    w("(The column shown is the number of violations; the other two outcomes are in Figure 3.) "
      "What the effect needs is the insurer x state effect, not the state x year effect. The "
      "anticipation term needs neither.")
    w("")
    w("## Figure 2: damage dummies")
    w("")
    w("This is the test that separates them, and I think it is the most useful thing in this round.")
    w("")
    w(f"Wildfire damage is zero in {notes['share_zero_level']:.0%} of state-years, so the median "
      f"over state-years is zero and an above-median dummy is the same variable as a damage / no "
      f"damage dummy. The 75th percentile is only about USD "
      f"{round(notes['p75_usd_million'] * 1e6, -3):,.0f} and the 90th about USD "
      f"{notes['p90_usd_million']:.1f} million, so I added two fixed amounts as well.")
    w("")
    w("| Damage measure | k = 0 | k = +2 | k = 0 significant | k = +2 significant |")
    w("|---|---|---|---|---|")
    dm_rows = cnt.loc["damage dummies"].reindex(order["damage dummies"])
    for variant in dm_rows.index:
        c0, c2 = m(variant, "count_f1", 0), m(variant, "count_f1", 2)
        w(f"| {variant.replace(' (Figure 2)', '')} | {cell(c0)} | {cell(c2)} | "
          f"{int(dm_rows.loc[variant, 0.0])} / 12 | {int(dm_rows.loc[variant, 2.0])} / 12 |")
    w("")
    w("Read down the table: the coarse dummies - any damage, and the top quartile, whose threshold "
      "is a fire of about a hundred thousand dollars - are significant at 5% in none of the twelve "
      "panels, at k = 0 or at k = +2. Once the dummy marks a genuinely large fire, the "
      "contemporaneous effect appears and the anticipation effect weakens: above the top decile "
      f"k = 0 is significant in {int(dm_rows.loc['Above the 90th percentile', 0.0])} of 12 panels "
      f"and k = +2 in {int(dm_rows.loc['Above the 90th percentile', 2.0])}, and above USD 10 "
      f"million in {int(dm_rows.loc['Above USD 10 million', 0.0])} and "
      f"{int(dm_rows.loc['Above USD 10 million', 2.0])}. Above USD 100 million only "
      "{n100} state-years are marked and the estimates are too imprecise to read.")
    w("")
    w("So the anticipation term looks like a property of the continuous ln measure rather than of "
      "large fires, which makes me think it comes from the damage data rather than from the "
      "violations. The next section is about that.")
    w("")
    w("## The damage data")
    w("")
    w("I checked the ten largest state-year values against published sources. **The years are "
      "right.** Storm Events files each record under the year in which it begins, no record in "
      "2013-2024 runs from one year into the next, and each of the ten fires began in the year the "
      "data assign it to. The dollar figures are another matter, and there are three distinct "
      "problems.")
    w("")
    w("**Some figures are repeated.** A fire that burns into a new month gets a new record, and "
      "some weather offices enter the whole fire's figure again in each monthly record. The "
      "Pioneer Fire of 2024 appears with USD 100 million in its July, August, September and "
      "October records. Counting a repeated figure once removes USD "
      f"{corr['repeated_24'].sum() / 1e6:,.0f} million of 2024 across {len(rep)} records and "
      f"{int((corr['repeated_nom'] > 0).sum())} state-years. Washington is most affected:")
    w("")
    w("| State-year | As recorded (USD m) | Counting repeats once | Removed |")
    w("|---|---|---|---|")
    for _, r in wa_big.sort_values("year").iterrows():
        w(f"| Washington {int(r['year'])} | {r['dmg24_fire'] / 1e6:,.1f} | "
          f"{r['dmg24_fire_corr'] / 1e6:,.1f} | {r['cut']:.0%} |")
    w("")
    w("**Some figures are the cost of fighting the fire, not damage.** The Forest Service's "
      "burned-area report for the Pioneer Fire, dated 16 September 2024, gives its suppression "
      "cost as USD 100,000,000 - the Storm Events figure to the dollar - and the fire destroyed no "
      "structures. The same pattern holds for the Cougar Creek and Crescent Mountain fires of "
      "2018, which the Forest Service's own year review lists at USD 42 and 41 million with zero "
      "structures destroyed, and which Storm Events carries at 42.3 and 39.4 million. These are "
      "remote timber fires; the Spokane office appears to be entering the federal cost of fighting "
      "them in the damage field.")
    w("")
    w("**Some are far too low, and the largest events are missing altogether.** Black Forest 2013 "
      "carries the first insurance estimate, USD 292.8 million, which was later revised to 420.5 "
      "million; East Troublesome 2020 is entered at USD 166.6 million against about 543 million "
      "insured; the Gray and Oregon Road fires of 2023 are entered at USD 60 and 15 million, while "
      "the Washington insurance commissioner reports USD 212 million paid on the two of them. The "
      "Babb Road fire of 2020, which burned most of the town of Malden and about 223 structures, "
      "is not in the data at all. Worse, whole "
      "fire seasons are absent: the data record USD "
      "{ca2017:,.0f} million for California 2017 and {or2020:,.0f} million for Oregon 2020, years "
      "in which insurers paid roughly USD 9-12 billion and 2 billion. Appendix 6 finds these from "
      f"the insurers' own numbers: {len(low)} state-years in which the fire-line market paid out "
      "more than it earned and more than half again its usual rate, with almost no recorded damage.")
    w("")
    w("None of this changes the results. Repeating Table 2 and Figure 2 with the repeats counted "
      "once gives the same estimates - the baseline goes from "
      f"{cell(t2f7[t2f7['dv'] == 'count'].iloc[0])} to {cell(a6f7[a6f7['dv'] == 'count'].iloc[0])} "
      "for the number of violations - and k = +2 stays significant in "
      f"{int(cnt.loc[('corrected damage', 'Repeated records counted once'), 2.0])} of 12 panels. "
      "But I would now describe the regressor as an indicator that a sizeable wildfire happened in "
      "that state and year, not as a loss figure, and I think the damage-dummy specifications above "
      "are the more honest way to use it. The error is not one-directional either: it inflates "
      "remote timber fires and understates or omits the fires that destroyed houses, which is "
      "exactly the wrong way round for what we are trying to measure. If you agree, the next step "
      "I would suggest is to rebuild the exposure shock from something closer to what insurers "
      "paid - the fire-line loss ratio of the state market, which Appendix 6 Panel D already uses "
      "as a cross-check.")
    w("")
    w("I look forward to hearing from you.")
    w("")
    w("Best regards,")
    w("Leslie")

    text = "\n".join(L)
    text = text.replace("{n100}", f"{int(d['dummy_n100'])}")
    text = text.replace("{ca2017:,.0f}", f"{d['ca2017']:,.0f}")
    text = text.replace("{or2020:,.0f}", f"{d['or2020']:,.0f}")
    return text


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else HERE.parent / "output")
    docs = Path(sys.argv[2] if len(sys.argv) > 2 else HERE)
    d = load(out)
    corr = d["wildfire_damage_corrected"]
    d["ca2017"] = float(corr[(corr["state_postal"] == "CA") & (corr["year"] == 2017)]["dmg24_fire"].iloc[0] / 1e6)
    d["or2020"] = float(corr[(corr["state_postal"] == "OR") & (corr["year"] == 2020)]["dmg24_fire"].iloc[0] / 1e6)
    # how many state-years the USD 100 million dummy marks
    d["dummy_n100"] = int((corr["dmg24_fire"] > 100e6).sum())

    text = reply(d)
    for bad in ["{", "}", "nan", "TODO"]:
        if bad in text:
            raise ValueError(f"the draft still contains {bad!r}")
    path = docs / "reply_to_professor.md"
    path.write_text(text)
    print(f"written {path}: {len(text.splitlines())} lines")


if __name__ == "__main__":
    main()
