# Supercoachv3

This repository includes a lightweight AFL SuperCoach 2026 CSV generator:

- Source prices: `https://www.footywire.com/afl/footy/supercoach_prices`
- Output file (repo root): `supercoach_2026_output.csv`
- Script: `python generate_supercoach_csv.py`

Model assumptions included in the script:

- Rookies (lower-priced players) are projected to have stronger early price growth.
- Young/mid-priced players are given a development uplift.
- Premium players receive a smaller consistency uplift.

A GitHub Actions workflow runs on every pull request and regenerates the CSV in the repository root.
