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


def _find_player_table(tables: List[List[List[str]]]) -> List[Dict[str, str]]:
    for table in tables:
        if not table:
            continue
        headers = [_normalize_header(h) for h in table[0]]
        if not headers:
            continue
        if "player" in headers and "price" in headers:
            records: List[Dict[str, str]] = []
            for row in table[1:]:
                if len(row) != len(headers):
                    continue
                records.append({headers[i]: row[i] for i in range(len(headers))})
            return records
    return []


def build_recommendations(html: str) -> List[Dict[str, str]]:
    parser = _TableParser()
    parser.feed(html)
    players = _find_player_table(parser.tables)
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    recommendations: List[Dict[str, str]] = []
    for player in players:
        name = player.get("player", "").strip()
        if not name:
            continue

        price = _to_price(player.get("price", ""))
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
            }
        )

    recommendations.sort(key=lambda row: float(row["value_score"]), reverse=True)
    return recommendations


def write_csv(rows: List[Dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


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
    args = parser.parse_args()

    html = _read_html(args.input_html)
    rows = build_recommendations(html)
    write_csv(rows, Path(args.output))

    print(f"Generated {len(rows)} rows at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
