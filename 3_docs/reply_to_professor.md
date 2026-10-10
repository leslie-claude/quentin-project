Dear Quentin,

Thank you. The answers to your four questions are below; the tables are attached and the code and data go with this message in one folder.

## Reporting the lower-order terms

Yes - exposure and damage have always been in every regression; I had simply not printed them. Every table now reports them, and writes 'absorbed' where the fixed effects take them out, so it is visible which design identifies what.

Printing them brought out one thing I had not noticed. Where ln damage is identified, its own coefficient is negative:

| | Exposure x Ln damage | Ln damage | Exposure |
|---|---|---|---|
| Number of violations, insurer + state + year | 0.2215** (0.0956) | -0.0067*** (0.0024) | 0.2288 (0.1968) |
| Ln penalties, insurer + state + year | 1.1531*** (0.3919) | -0.0421*** (0.0135) | 3.0157 (1.8543) |
| Any violation, insurer + state + year | 0.0922*** (0.0310) | -0.0040*** (0.0013) | 0.2695* (0.1557) |

So in that design a wildfire raises violations only for an insurer whose market share is above about 3.0% (the number of violations), 3.6% (ln penalties) or 4.3% (any violation), against a mean share of 2.0% and a median of 0.5%. For a small insurer the sign goes the other way. I do not think the level is where the identification is - under insurer x state and state x year effects, which is my preferred design, state x year effects absorb damage entirely and the question does not arise - but it is worth knowing.

## Figure 2: size and market concentration

Controlling for ln of the insurer's premiums in the state, for the fire-line Herfindahl index of the state and year, and for market share x that index (which state x year effects do not absorb) leaves the k = +2 term where it was:

| Control | k = 0 | k = +2 |
|---|---|---|
| No controls | 0.1733** (0.0834) | 0.1194** (0.0467) |
| Ln insurer size | 0.1732** (0.0834) | 0.1193** (0.0467) |
| Ln insurer size + HHI | 0.1732** (0.0834) | 0.1193** (0.0467) |
| Ln insurer size + Market share × HHI | 0.1730** (0.0831) | 0.1167** (0.0430) |

Across the twelve panels of Figure 2 (three outcomes x two exposure measures x two windows), k = +2 is significant at 5% in 11 of 12 in every variant.

## Figure 2: other fixed effects

I ran it under all 11 combinations of insurer, state, year, insurer x state, insurer x year and state x year effects. The anticipation term survives all of them: k = +2 is significant at 5% in 10 to 11 of the twelve panels under every design.

The term that is NOT robust is the contemporaneous one. k = 0 is significant in 10-11 of 12 only in the designs that contain insurer x state effects, and in 0-3 of 12 in the designs that do not:

| Fixed effects | k = 0 | k = +2 | k = 0 significant | k = +2 significant |
|---|---|---|---|---|
| Year | 0.0290 (0.1052) | 0.1083*** (0.0343) | 0 / 12 | 10 / 12 |
| Insurer + Year | 0.0268 (0.1036) | 0.1098*** (0.0371) | 0 / 12 | 10 / 12 |
| Insurer + State + Year | 0.0366 (0.0975) | 0.1213*** (0.0330) | 3 / 12 | 10 / 12 |
| Insurer × Year | 0.0263 (0.0984) | 0.1052*** (0.0368) | 0 / 12 | 10 / 12 |
| Insurer × Year + State | 0.0356 (0.0923) | 0.1173*** (0.0336) | 3 / 12 | 10 / 12 |
| Insurer + State × Year | 0.0381 (0.0991) | 0.1182*** (0.0321) | 1 / 12 | 10 / 12 |
| Insurer × State + State × Year | 0.1733** (0.0834) | 0.1194** (0.0467) | 11 / 12 | 11 / 12 |
| Insurer × Year + State × Year | 0.0377 (0.0944) | 0.1141*** (0.0322) | 2 / 12 | 10 / 12 |
| Insurer × State + Insurer × Year | 0.1486* (0.0772) | 0.1016** (0.0470) | 10 / 12 | 11 / 12 |
| Insurer × State + Insurer × Year + State × Year | 0.1617* (0.0814) | 0.1047** (0.0437) | 10 / 12 | 10 / 12 |
| Insurer × State + Year | 0.1591* (0.0794) | 0.1142** (0.0492) | 10 / 12 | 11 / 12 |

(The column shown is the number of violations; the other two outcomes are in Figure 3.) What the effect needs is the insurer x state effect, not the state x year effect. The anticipation term needs neither.

## Figure 2: damage dummies

This is the test that separates them, and I think it is the most useful thing in this round.

Wildfire damage is zero in 68% of state-years, so the median over state-years is zero and an above-median dummy is the same variable as a damage / no damage dummy. The 75th percentile is only about USD 110,000 and the 90th about USD 5.4 million, so I added two fixed amounts as well.

| Damage measure | k = 0 | k = +2 | k = 0 significant | k = +2 significant |
|---|---|---|---|---|
| Ln wildfire damage | 0.1733** (0.0834) | 0.1194** (0.0467) | 11 / 12 | 11 / 12 |
| Any damage (above the median) | 0.6302* (0.3485) | 0.3951* (0.2313) | 0 / 12 | 0 / 12 |
| Above the 75th percentile | 0.6402 (0.4891) | 0.3241 (0.3288) | 0 / 12 | 0 / 12 |
| Above the 90th percentile | 1.3032* (0.7112) | 0.5632 (0.8217) | 10 / 12 | 0 / 12 |
| Above USD 10 million | 1.1171** (0.4554) | 0.3174 (0.2159) | 12 / 12 | 5 / 12 |
| Above USD 100 million | 0.3395 (1.1158) | 0.0676 (0.7675) | 5 / 12 | 6 / 12 |

Read down the table: the coarse dummies - any damage, and the top quartile, whose threshold is a fire of about a hundred thousand dollars - are significant at 5% in none of the twelve panels, at k = 0 or at k = +2. Once the dummy marks a genuinely large fire, the contemporaneous effect appears and the anticipation effect weakens: above the top decile k = 0 is significant in 10 of 12 panels and k = +2 in 0, and above USD 10 million in 12 and 5. Above USD 100 million only 14 state-years are marked and the estimates are too imprecise to read.

So the anticipation term looks like a property of the continuous ln measure rather than of large fires, which makes me think it comes from the damage data rather than from the violations. The next section is about that.

## The damage data

I checked the ten largest state-year values against published sources. **The years are right.** Storm Events files each record under the year in which it begins, no record in 2013-2024 runs from one year into the next, and each of the ten fires began in the year the data assign it to. The dollar figures are another matter, and there are three distinct problems.

**Some figures are repeated.** A fire that burns into a new month gets a new record, and some weather offices enter the whole fire's figure again in each monthly record. The Pioneer Fire of 2024 appears with USD 100 million in its July, August, September and October records. Counting a repeated figure once removes USD 886 million of 2024 across 69 records and 21 state-years. Washington is most affected:

| State-year | As recorded (USD m) | Counting repeats once | Removed |
|---|---|---|---|
| Washington 2014 | 324.0 | 164.9 | 49% |
| Washington 2022 | 368.8 | 208.8 | 43% |
| Washington 2023 | 191.5 | 109.6 | 43% |
| Washington 2024 | 548.4 | 217.1 | 60% |

**Some figures are the cost of fighting the fire, not damage.** The Forest Service's burned-area report for the Pioneer Fire, dated 16 September 2024, gives its suppression cost as USD 100,000,000 - the Storm Events figure to the dollar - and the fire destroyed no structures. The same pattern holds for the Cougar Creek and Crescent Mountain fires of 2018, which the Forest Service's own year review lists at USD 42 and 41 million with zero structures destroyed, and which Storm Events carries at 42.3 and 39.4 million. These are remote timber fires; the Spokane office appears to be entering the federal cost of fighting them in the damage field.

**Some are far too low, and the largest events are missing altogether.** Black Forest 2013 carries the first insurance estimate, USD 292.8 million, which was later revised to 420.5 million; East Troublesome 2020 is entered at USD 166.6 million against about 543 million insured; the Gray and Oregon Road fires of 2023 are entered at USD 60 and 15 million, while the Washington insurance commissioner reports USD 212 million paid on the two of them. The Babb Road fire of 2020, which burned most of the town of Malden and about 223 structures, is not in the data at all. Worse, whole fire seasons are absent: the data record USD 123 million for California 2017 and 12 million for Oregon 2020, years in which insurers paid roughly USD 9-12 billion and 2 billion. Appendix 6 finds these from the insurers' own numbers: 18 state-years in which the fire-line market paid out more than it earned and more than half again its usual rate, with almost no recorded damage.

None of this changes the results. Repeating Table 2 and Figure 2 with the repeats counted once gives the same estimates - the baseline goes from 0.1681*** (0.0367) to 0.1699*** (0.0367) for the number of violations - and k = +2 stays significant in 11 of 12 panels. But I would now describe the regressor as an indicator that a sizeable wildfire happened in that state and year, not as a loss figure, and I think the damage-dummy specifications above are the more honest way to use it. The error is not one-directional either: it inflates remote timber fires and understates or omits the fires that destroyed houses, which is exactly the wrong way round for what we are trying to measure. If you agree, the next step I would suggest is to rebuild the exposure shock from something closer to what insurers paid - the fire-line loss ratio of the state market, which Appendix 6 Panel D already uses as a cross-check.

I look forward to hearing from you.

Best regards,
Leslie