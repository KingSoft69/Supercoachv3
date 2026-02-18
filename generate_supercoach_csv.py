#!/usr/bin/env python3
import argparse
import csv
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List
from urllib.request import Request, urlopen

SOURCE_URL = "https://www.footywire.com/afl/footy/supercoach_prices"
DEFAULT_OUTPUT = "supercoach_2026_output.csv"
SC_PRICE_TO_AVG_RATIO = 5400.0
DEFAULT_SALARY_CAP = 10_000_000
DEFAULT_TEAM_SIZE = 30
DEFAULT_MAX_PLAYERS_PER_BYE = 8
SEASON_ROUNDS = 24
DEFAULT_BYES_PER_PLAYER = 1


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: List[List[List[str]]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_table: List[List[str]] = []
        self._current_row: List[str] = []
        self._current_cell: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._current_row = []
        elif self._in_row and tag in {"th", "td"}:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._in_cell:
            text = " ".join("".join(self._current_cell).split())
            self._current_row.append(text)
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._in_row = False
        elif tag == "table" and self._in_table:
            if self._current_table:
                self.tables.append(self._current_table)
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)


def _to_number(value: str) -> float:
    cleaned = re.sub(r"[^0-9.\-]", "", value or "")
    return float(cleaned) if cleaned else 0.0


def _to_price(value: str) -> int:
    return int(_to_number(value))


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _growth_factor(price: int) -> float:
    if price <= 180000:
        return 1.25  # rookie price growth potential
    if price <= 260000:
        return 1.15  # likely early-career breakout
    if price <= 430000:
        return 1.10  # young player development trend
    if price >= 550000:
        return 1.03  # premium consistency bump
    return 1.0


def _parse_bye_round(player: Dict[str, str]) -> str:
    bye_col = next((k for k in player.keys() if "bye" in k), "")
    if not bye_col:
        return ""
    bye_round = int(_to_number(player.get(bye_col, "")))
    return str(bye_round) if bye_round > 0 else ""


def _select_team(
    rows: List[Dict[str, str]], salary_cap: int, team_size: int, max_players_per_bye: int
) -> List[int]:
    ordered = sorted(
        range(len(rows)),
        key=lambda i: (
            float(rows[i]["projected_season_points"]),
            float(rows[i]["projected_season_points"]) / max(int(rows[i]["price"]), 1),
            float(rows[i]["value_score"]),
        ),
        reverse=True,
    )
    selected: List[int] = []
    total_price = 0
    bye_counts: Dict[str, int] = {}
    for idx in ordered:
        if len(selected) >= team_size:
            break
        row = rows[idx]
        price = int(row["price"])
        bye_round = row["bye_round"]
        if total_price + price > salary_cap:
            continue
        remaining_slots = team_size - len(selected) - 1
        if remaining_slots > 0:
            remaining_prices = sorted(
                int(rows[j]["price"]) for j in ordered if j != idx and j not in selected
            )
            if len(remaining_prices) < remaining_slots:
                continue
            min_required = sum(remaining_prices[:remaining_slots])
            if total_price + price + min_required > salary_cap:
                continue
        if bye_round and bye_counts.get(bye_round, 0) >= max_players_per_bye:
            continue
        selected.append(idx)
        total_price += price
        if bye_round:
            bye_counts[bye_round] = bye_counts.get(bye_round, 0) + 1
    return selected


def _find_player_table(tables: List[List[List[str]]]) -> List[Dict[str, str]]:
    for table in tables:
        if not table:
            continue
        headers = [_normalize_header(h) for h in table[0]]
        if not headers:
            continue
        if "player" in headers and ("price" in headers or "current" in headers):
            records: List[Dict[str, str]] = []
            for row in table[1:]:
                if len(row) != len(headers):
                    continue
                records.append({headers[i]: row[i] for i in range(len(headers))})
            return records
    return []


def build_recommendations(
    html: str,
    salary_cap: int = DEFAULT_SALARY_CAP,
    team_size: int = DEFAULT_TEAM_SIZE,
    max_players_per_bye: int = DEFAULT_MAX_PLAYERS_PER_BYE,
) -> List[Dict[str, str]]:
    parser = _TableParser()
    parser.feed(html)
    players = _find_player_table(parser.tables)
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    recommendations: List[Dict[str, str]] = []
    for player in players:
        name = player.get("player", "").strip()
        if not name:
            continue

        price = _to_price(player.get("price") or player.get("current", ""))
        if price <= 0:
            continue

        avg_col = next((k for k in player.keys() if "avg" in k), "")
        current_avg = _to_number(player.get(avg_col, "")) if avg_col else 0.0
        if current_avg <= 0:
            current_avg = price / SC_PRICE_TO_AVG_RATIO

        factor = _growth_factor(price)
        projected_avg = current_avg * factor
        projected_price_gain = int(price * (factor - 1.0))
        value_score = projected_avg / (price / 100000.0)
        bye_round = _parse_bye_round(player)
        projected_season_points = projected_avg * (SEASON_ROUNDS - DEFAULT_BYES_PER_PLAYER)

        recommendations.append(
            {
                "generated_at_utc": timestamp,
                "source_url": SOURCE_URL,
                "player": name,
                "team": player.get("team", ""),
                "position": player.get("position", player.get("pos", "")),
                "price": str(price),
                "current_avg": f"{current_avg:.2f}",
                "projected_avg": f"{projected_avg:.2f}",
                "growth_factor": f"{factor:.2f}",
                "projected_price_gain": str(projected_price_gain),
                "value_score": f"{value_score:.4f}",
                "bye_round": bye_round,
                "projected_season_points": f"{projected_season_points:.2f}",
                "selected_for_team": "no",
                "selection_rank": "",
                "is_overall_winner": "no",
            }
        )

    selected_indexes = _select_team(
        recommendations,
        salary_cap=salary_cap,
        team_size=team_size,
        max_players_per_bye=max_players_per_bye,
    )
    selected_rows = sorted(
        selected_indexes,
        key=lambda i: float(recommendations[i]["projected_season_points"]),
        reverse=True,
    )
    for rank, idx in enumerate(selected_rows, start=1):
        recommendations[idx]["selected_for_team"] = "yes"
        recommendations[idx]["selection_rank"] = str(rank)
        if rank == 1:
            recommendations[idx]["is_overall_winner"] = "yes"

    recommendations.sort(key=lambda row: float(row["value_score"]), reverse=True)
    return recommendations


def write_csv(rows: List[Dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    selected_rows = [row for row in rows if row.get("selected_for_team") == "yes"]
    fields = [
        "generated_at_utc",
        "source_url",
        "player",
        "team",
        "position",
        "price",
        "current_avg",
        "projected_avg",
        "growth_factor",
        "projected_price_gain",
        "value_score",
        "bye_round",
        "projected_season_points",
        "selected_for_team",
        "selection_rank",
        "is_overall_winner",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(selected_rows)


def _read_html(input_html: str | None) -> str:
    if input_html:
        return Path(input_html).read_text(encoding="utf-8")

    request = Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="ignore")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate AFL SuperCoach 2026 recommendation CSV")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output CSV path")
    parser.add_argument("--input-html", help="Local HTML file for offline/testing use")
    parser.add_argument("--salary-cap", type=int, default=DEFAULT_SALARY_CAP, help="Team salary cap")
    parser.add_argument("--team-size", type=int, default=DEFAULT_TEAM_SIZE, help="Number of players to select")
    parser.add_argument(
        "--max-players-per-bye",
        type=int,
        default=DEFAULT_MAX_PLAYERS_PER_BYE,
        help="Maximum selected players sharing the same bye round",
    )
    args = parser.parse_args()

    html = _read_html(args.input_html)
    rows = build_recommendations(
        html,
        salary_cap=args.salary_cap,
        team_size=args.team_size,
        max_players_per_bye=args.max_players_per_bye,
    )
    write_csv(rows, Path(args.output))

    print(f"Generated {len(rows)} rows at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
