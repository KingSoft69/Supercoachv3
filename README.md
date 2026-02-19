# Supercoachv3

This repository includes a comprehensive AFL SuperCoach 2026 CSV generator:

- Source prices: `https://www.footywire.com/afl/footy/supercoach_prices`
- Output file (repo root): `supercoach_2026_output.csv`
- Script: `python generate_supercoach_csv.py`
- Team selection controls:
  - `--salary-cap` (default: 10000000)
  - `--team-size` (default: 30, includes bench)
  - `--max-players-per-bye` (default: 8)
  - `--skip-positions` (skip fetching per-position pages for faster offline runs)

## Data scraping

The script scrapes the footywire SuperCoach prices page and extracts:

- **Player names** – clean full names from hidden spans (not concatenated abbreviations)
- **Team** – mapped to standard 3-letter codes (e.g. WBD, MEL, COL)
- **Positions** – scraped from per-position pages (DEF, MID, FWD, RUC) with dual-position support (e.g. MID/FWD)
- **Current price** and **expected future prices** used to inform growth projections

## Model assumptions

- Rookies (lower-priced players) are projected to have stronger early price growth.
- Young/mid-priced players are given a development uplift.
- Premium players receive a smaller consistency uplift.
- Expected price data from footywire is blended with base growth factors for more accurate projections.
- Player news factors adjust for injuries, breakouts, and comeback candidates (e.g. Joshua Kelly injury, Jagga Smith breakout lock).
- Team selection respects salary cap, bye-round limits, and **position limits** (DEF ≤ 9, MID ≤ 11, RUC ≤ 4, FWD ≤ 10).
- Output CSV includes selected team rows only.
- `is_overall_winner=yes` marks the top projected selected player for end-of-season output.

## Running the workflow

A GitHub Actions workflow regenerates the CSV automatically on every pull request. You can also run it manually without creating a PR:

1. Go to the **Actions** tab in this repository.
2. Select the **Update SuperCoach CSV** workflow on the left.
3. Click the **Run workflow** button, choose a branch, and confirm.
4. Once the run completes, the updated CSV will be committed directly to the repository.
