Data read by 2_code/analysis.py

insurer_line_state_year.zip
    Insurer x business line x state x year, every insurer in the industry file: premiums
    written and earned, losses incurred, the CPI, and a flag for each line group (lg_*).
    Statutory filings, S&P Capital IQ. Money is in constant 2024 dollars. The script reads
    the csv straight out of the zip.

insurer_state_year.csv
    The estimation panel: the 38 insurer groups linked to Violation Tracker x 51 states
    (including DC) x 2013-2024, 23,256 rows. Premiums by line group, the sample flags, and
    state-year damage by hazard from NOAA Storm Events, deflated to 2024 dollars
    (lndmg_* = ln(1 + damage in USD million)).

violations_insurer_state_year.csv
    Insurance violations and the penalties imposed, by insurer, state and penalty year,
    2010-2025. Violation Tracker (Good Jobs First), Global edition, 29 September 2025.

violation_records.csv
    The 4,038 Violation Tracker records linked to those 38 insurers, one row each, with the
    offense category and the flags used to split insurance from other violations.

rstreet_scores_2024.csv
    Tables 5 (rate regulation, by line of business) and 6 (underwriting freedom) of the
    R Street 2024 Insurance Regulation Report Card, one row per state, read off
    r_street_policy_study_314.pdf. A higher score means less regulation.

storm_events_wildfire_2013_2024.csv
    Every record with EVENT_TYPE "Wildfire" in the NOAA Storm Events details files for
    2013-2024, columns unchanged, including the narratives. Their property and crop damage
    add up to the wildfire damage in insurer_state_year.csv, and the script checks that.

damage_check_sources.csv
    Published damage figures for the fires behind the ten largest state-year values, with
    their sources, looked up by state and year. Used in Appendix 6.

r_street_policy_study_314.pdf
    The report the regulation scores are read from, kept so they can be checked.

parts/
    insurer_line_state_year.zip is 114 MiB, over the 100 MB a file may be on GitHub, so the
    repository carries the csv in six pieces instead: 1-200000 holds the header and the first
    199,999 rows, then 20-400000, 40-600000, 60-800000, 80-1000000 and 100000.csv hold the
    rest in order, 1,180,812 rows of 98 fields in all. To rebuild the zip the script reads:

        cd parts
        for p in 1-200000 20-400000 40-600000 60-800000 80-1000000 100000.csv; do
            unzip -p "$p.zip" "$p"
        done > ../insurer_line_state_year.csv
        cd .. && zip -9 insurer_line_state_year.zip insurer_line_state_year.csv
