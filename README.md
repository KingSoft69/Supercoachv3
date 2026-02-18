# Supercoachv3

This repository includes a lightweight AFL SuperCoach 2026 CSV generator:

- Source prices: `https://www.footywire.com/afl/footy/supercoach_prices`
- Output file (repo root): `supercoach_2026_output.csv`
- Script: `python generate_supercoach_csv.py`
- Team selection controls:
  - `--salary-cap` (default: 10000000)
  - `--team-size` (default: 30, includes bench)
  - `--max-players-per-bye` (default: 8)

Model assumptions included in the script:

- Rookies (lower-priced players) are projected to have stronger early price growth.
- Young/mid-priced players are given a development uplift.
- Premium players receive a smaller consistency uplift.
- Team selection respects salary cap and limits concentrated bye-round exposure.
- Output CSV includes selected team rows only.
- `is_overall_winner=yes` marks the top projected selected player for end-of-season output.

A GitHub Actions workflow runs on every pull request and regenerates the CSV in the repository root.
