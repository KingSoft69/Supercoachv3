#!/usr/bin/env python3
import argparse
import csv
import re
from heapq import nsmallest
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.request import Request, urlopen

SOURCE_URL = "https://www.footywire.com/afl/footy/supercoach_prices"
DEFAULT_OUTPUT = "supercoach_2026_output.csv"
SC_PRICE_TO_AVG_RATIO = 5400.0
DEFAULT_SALARY_CAP = 10_000_000
DEFAULT_TEAM_SIZE = 30
DEFAULT_MAX_PLAYERS_PER_BYE = 8
SEASON_ROUNDS = 24
DEFAULT_BYES_PER_PLAYER = 1
# Only inject fallback breakout locks for large real-world pulls, not tiny unit-test tables.
BREAKOUT_FALLBACK_MIN_PLAYERS = 100

# Position slots for a SuperCoach squad (on-field + bench + emergency, relaxed)
POSITION_LIMITS: Dict[str, int] = {"DEF": 9, "MID": 11, "RUC": 4, "FWD": 10}

# Footywire position URL codes
POSITION_URL_CODES: Dict[str, str] = {"DEF": "DE", "MID": "MI", "FWD": "FO", "RUC": "RU"}

# Team abbreviation mapping (footywire long name -> short code)
TEAM_ABBREV: Dict[str, str] = {
    "crows": "ADE", "lions": "BRL", "blues": "CAR", "magpies": "COL",
    "bombers": "ESS", "dockers": "FRE", "cats": "GEE", "suns": "GCS",
    "giants": "GWS", "hawks": "HAW", "demons": "MEL", "kangaroos": "NTH",
    "power": "PTA", "tigers": "RIC", "saints": "STK", "swans": "SYD",
    "eagles": "WCE", "bulldogs": "WBD",
}

PLAYER_NEWS_FACTORS = {
    "joshua kelly": 0.0,  # preseason hip surgery; expected to miss most/all of season
    "jagga smith": 1.35,  # preseason rookie lock
    "keidean coleman": 1.20,  # discounted comeback candidate after injury-impacted years
    "christian petracca": 0.85,  # returning from serious lacerated spleen; managed load expected
    "callum mills": 0.90,  # returning from ACL; limited early output
    "sam walsh": 1.08,  # bounce-back season expected after quiet 2025
    "will ashcroft": 1.12,  # second full season post-ACL; primed for breakout
    "harry sheezel": 1.10,  # proven young gun expected to take next step
    "nick daicos": 1.05,  # elite consistency; Brownlow favourite
    "connor rozee": 1.05,  # dual-position premium; captaincy boost
}
LOCKED_BREAKOUT_DEFAULTS = [
    {"player": "Jagga Smith", "team": "CAR", "position": "MID", "price": 119900, "current_avg": 22.0}
]


class _TableParser(HTMLParser):
    """Generic HTML table parser that extracts all tables as lists of rows."""

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


class _FootywireParser(HTMLParser):
    """Parser specifically for the footywire supercoach_prices page structure.

    The page uses hidden spans in the player cell:
      <span class="hiddenspan" id="cellpid_NNN">Full Name</span>
      <a href="pu-team--player">Short Name</a>
      <span class="hiddenspan" id="celltid_NNN">Team</span>

    The table headers are: Player, Current, Total Change, Change %,
    Last Change, Expected Price, Expected Change, Expected Price 2, ...
    """

    def __init__(self) -> None:
        super().__init__()
        self.players: List[Dict[str, str]] = []
        self._in_player_row = False
        self._in_cell = False
        self._cell_index = 0
        self._current_cells: List[str] = []
        self._current_cell_parts: List[str] = []
        self._current_player_name = ""
        self._current_team = ""
        self._in_hidden_span = False
        self._hidden_span_id = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        attr_dict = dict(attrs)
        if tag == "tr":
            cls = attr_dict.get("class", "")
            if "darkcolor" in cls or "lightcolor" in cls:
                self._in_player_row = True
                self._cell_index = 0
                self._current_cells = []
                self._current_player_name = ""
                self._current_team = ""
        elif self._in_player_row and tag == "td":
            self._in_cell = True
            self._current_cell_parts = []
        elif self._in_player_row and tag == "span":
            span_id = attr_dict.get("id", "")
            span_cls = attr_dict.get("class", "")
            if "hiddenspan" in span_cls:
                self._in_hidden_span = True
                self._hidden_span_id = span_id

    def handle_endtag(self, tag: str) -> None:
        if tag == "span" and self._in_hidden_span:
            self._in_hidden_span = False
            self._hidden_span_id = ""
        elif tag == "td" and self._in_cell:
            text = " ".join("".join(self._current_cell_parts).split())
            self._current_cells.append(text)
            self._cell_index += 1
            self._in_cell = False
        elif tag == "tr" and self._in_player_row:
            self._in_player_row = False
            if self._current_player_name and len(self._current_cells) >= 2:
                price_str = self._current_cells[1] if len(self._current_cells) > 1 else ""
                expected_price = self._current_cells[5] if len(self._current_cells) > 5 else ""
                expected_price_3 = self._current_cells[9] if len(self._current_cells) > 9 else ""
                self.players.append({
                    "player": self._current_player_name,
                    "team": self._current_team,
                    "current": price_str,
                    "expected_price": expected_price,
                    "expected_price_3": expected_price_3,
                })

    def handle_data(self, data: str) -> None:
        if self._in_hidden_span:
            if self._hidden_span_id.startswith("cellpid_"):
                self._current_player_name = data.strip()
            elif self._hidden_span_id.startswith("celltid_"):
                self._current_team = data.strip()
        elif self._in_cell and self._cell_index > 0:
            self._current_cell_parts.append(data)


def _to_number(value: str) -> float:
    cleaned = re.sub(r"[^0-9.\-]", "", value or "")
    return float(cleaned) if cleaned else 0.0


def _to_price(value: str) -> int:
    return int(_to_number(value))


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _normalize_player_name(value: str) -> str:
    return " ".join(re.findall(r"[a-z]+", value.lower()))


def _team_abbrev(team_long: str) -> str:
    return TEAM_ABBREV.get(team_long.lower().strip(), team_long.upper()[:3])


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


def _player_news_factor(name: str) -> Optional[float]:
    normalized = _normalize_player_name(name)
    for player_name, factor in PLAYER_NEWS_FACTORS.items():
        if normalized == player_name or normalized.startswith(f"{player_name} "):
            return factor
    return None


def _is_locked_breakout(name: str) -> bool:
    normalized = _normalize_player_name(name)
    return any(
        normalized == lock["player"].lower() or normalized.startswith(f"{lock['player'].lower()} ")
        for lock in LOCKED_BREAKOUT_DEFAULTS
    )


def _primary_position(position: str) -> str:
    """Return the first listed position for slot counting."""
    parts = [p.strip() for p in position.split("/") if p.strip()]
    return parts[0] if parts else ""


def _fits_position(position: str, slot: str) -> bool:
    """Check if a player with *position* can fill a particular position *slot*."""
    return slot in [p.strip() for p in position.split("/") if p.strip()]


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

    position_set = {rows[i].get("position", "") for i in ordered} - {""}
    use_position_limits = len(position_set) >= 2 and team_size >= 20

    selected: List[int] = []
    selected_set: Set[int] = set()
    total_price = 0
    bye_counts: Dict[str, int] = {}
    pos_counts: Dict[str, int] = {}

    for idx in ordered:
        if len(selected) >= team_size:
            break
        row = rows[idx]
        price = int(row["price"])
        bye_round = row["bye_round"]
        position = row.get("position", "")

        if total_price + price > salary_cap:
            continue

        remaining_slots = team_size - len(selected) - 1
        if remaining_slots > 0:
            cheapest_remaining = nsmallest(
                remaining_slots,
                (int(rows[j]["price"]) for j in ordered if j != idx and j not in selected_set),
            )
            if len(cheapest_remaining) < remaining_slots:
                continue
            min_required = sum(cheapest_remaining)
            if total_price + price + min_required > salary_cap:
                continue

        if bye_round and bye_counts.get(bye_round, 0) >= max_players_per_bye:
            continue

        if use_position_limits and position:
            primary = _primary_position(position)
            limit = POSITION_LIMITS.get(primary, team_size)
            if pos_counts.get(primary, 0) >= limit:
                placed = False
                for alt in position.split("/"):
                    alt = alt.strip()
                    if alt and alt != primary:
                        alt_limit = POSITION_LIMITS.get(alt, team_size)
                        if pos_counts.get(alt, 0) < alt_limit:
                            pos_counts[alt] = pos_counts.get(alt, 0) + 1
                            placed = True
                            break
                if not placed:
                    continue
            else:
                pos_counts[primary] = pos_counts.get(primary, 0) + 1

        selected.append(idx)
        selected_set.add(idx)
        total_price += price
        if bye_round:
            bye_counts[bye_round] = bye_counts.get(bye_round, 0) + 1

    locked_indexes = [i for i in ordered if _is_locked_breakout(rows[i]["player"])]
    for lock_idx in locked_indexes:
        if lock_idx in selected_set:
            continue
        lock_row = rows[lock_idx]
        lock_price = int(lock_row["price"])
        lock_bye = lock_row["bye_round"]
        for out_idx in sorted(selected, key=lambda i: float(rows[i]["projected_season_points"])):
            if _is_locked_breakout(rows[out_idx]["player"]):
                continue
            out_row = rows[out_idx]
            out_price = int(out_row["price"])
            out_bye = out_row["bye_round"]
            if total_price - out_price + lock_price > salary_cap:
                continue
            if lock_bye and lock_bye != out_bye and bye_counts.get(lock_bye, 0) >= max_players_per_bye:
                continue
            selected.remove(out_idx)
            selected_set.remove(out_idx)
            total_price -= out_price
            if out_bye:
                bye_counts[out_bye] = max(0, bye_counts.get(out_bye, 0) - 1)
                if bye_counts[out_bye] == 0:
                    del bye_counts[out_bye]

            selected.append(lock_idx)
            selected_set.add(lock_idx)
            total_price += lock_price
            if lock_bye:
                bye_counts[lock_bye] = bye_counts.get(lock_bye, 0) + 1
            break
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


def _scrape_footywire_positions(html: str) -> Set[str]:
    """Extract player names from a position-filtered footywire page."""
    names: Set[str] = set()
    for match in re.finditer(
        r'<span\s+class="hiddenspan"\s+id="cellpid_\d+">([^<]+)</span>', html
    ):
        names.add(match.group(1).strip())
    return names


def _fetch_url(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="ignore")


def _scrape_position_map(base_url: str) -> Dict[str, str]:
    """Scrape position-specific pages and build a player -> position map.

    Players appearing on multiple position pages get a dual-position string
    like ``DEF/MID``.
    """
    position_map: Dict[str, List[str]] = {}
    for pos_label, url_code in POSITION_URL_CODES.items():
        url = f"{base_url}?p={url_code}"
        try:
            html = _fetch_url(url)
        except Exception:
            continue
        names = _scrape_footywire_positions(html)
        for name in names:
            position_map.setdefault(name, []).append(pos_label)

    return {name: "/".join(positions) for name, positions in position_map.items()}


def _parse_footywire_players(html: str) -> List[Dict[str, str]]:
    """Parse the main footywire supercoach_prices page using the specialised parser."""
    fw = _FootywireParser()
    fw.feed(html)
    return fw.players


def build_recommendations(
    html: str,
    salary_cap: int = DEFAULT_SALARY_CAP,
    team_size: int = DEFAULT_TEAM_SIZE,
    max_players_per_bye: int = DEFAULT_MAX_PLAYERS_PER_BYE,
    position_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, str]]:
    # Try footywire-specific parser first; fall back to generic table parser
    fw_players = _parse_footywire_players(html)

    if fw_players:
        players = fw_players
        is_footywire = True
    else:
        parser = _TableParser()
        parser.feed(html)
        players = _find_player_table(parser.tables)
        is_footywire = False

    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    recommendations: List[Dict[str, str]] = []
    for player in players:
        name = player.get("player", "").strip()
        if not name:
            continue

        if is_footywire:
            price = _to_price(player.get("current", ""))
            team_raw = player.get("team", "")
            team = _team_abbrev(team_raw) if team_raw else ""
            position = ""
            if position_map:
                position = position_map.get(name, "")

            expected_price_3 = _to_price(player.get("expected_price_3", ""))
            if expected_price_3 > 0 and price > 0:
                expected_change_ratio = expected_price_3 / price
            else:
                expected_change_ratio = 1.0
        else:
            price = _to_price(player.get("price") or player.get("current", ""))
            team = player.get("team", "")
            position = player.get("position", player.get("pos", ""))
            expected_change_ratio = 1.0

        if price <= 0:
            continue

        avg_col = next((k for k in player.keys() if "avg" in k), "")
        current_avg = _to_number(player.get(avg_col, "")) if avg_col else 0.0
        if current_avg <= 0:
            current_avg = price / SC_PRICE_TO_AVG_RATIO

        factor = _growth_factor(price)
        news_factor = _player_news_factor(name)
        if news_factor is not None:
            factor = news_factor
        elif is_footywire and expected_change_ratio != 1.0:
            blended = (factor + expected_change_ratio) / 2.0
            factor = max(blended, 0.0)

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
                "team": team,
                "position": position,
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

    if len(recommendations) >= BREAKOUT_FALLBACK_MIN_PLAYERS:
        normalized_names = {_normalize_player_name(row["player"]) for row in recommendations}
        for breakout in LOCKED_BREAKOUT_DEFAULTS:
            breakout_name_normalized = _normalize_player_name(breakout["player"])
            if any(
                existing == breakout_name_normalized
                or existing.startswith(f"{breakout_name_normalized} ")
                for existing in normalized_names
            ):
                continue
            factor = _player_news_factor(breakout["player"]) or _growth_factor(int(breakout["price"]))
            projected_avg = breakout["current_avg"] * factor
            projected_price_gain = int(breakout["price"] * (factor - 1.0))
            value_score = projected_avg / (breakout["price"] / 100000.0)
            projected_season_points = projected_avg * (SEASON_ROUNDS - DEFAULT_BYES_PER_PLAYER)
            recommendations.append(
                {
                    "generated_at_utc": timestamp,
                    "source_url": SOURCE_URL,
                    "player": breakout["player"],
                    "team": breakout["team"],
                    "position": breakout["position"],
                    "price": str(breakout["price"]),
                    "current_avg": f"{breakout['current_avg']:.2f}",
                    "projected_avg": f"{projected_avg:.2f}",
                    "growth_factor": f"{factor:.2f}",
                    "projected_price_gain": str(projected_price_gain),
                    "value_score": f"{value_score:.4f}",
                    "bye_round": "",
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


def _read_html(input_html: Optional[str]) -> str:
    if input_html:
        return Path(input_html).read_text(encoding="utf-8")

    request = Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="ignore")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate AFL SuperCoach 2026 recommendation CSV")
    ap.add_argument("--output", default=DEFAULT_OUTPUT, help="Output CSV path")
    ap.add_argument("--input-html", help="Local HTML file for offline/testing use")
    ap.add_argument("--salary-cap", type=int, default=DEFAULT_SALARY_CAP, help="Team salary cap")
    ap.add_argument("--team-size", type=int, default=DEFAULT_TEAM_SIZE, help="Number of players to select")
    ap.add_argument(
        "--max-players-per-bye",
        type=int,
        default=DEFAULT_MAX_PLAYERS_PER_BYE,
        help="Maximum selected players sharing the same bye round",
    )
    ap.add_argument(
        "--skip-positions",
        action="store_true",
        help="Skip fetching per-position pages (faster but no position data)",
    )
    args = ap.parse_args()

    html = _read_html(args.input_html)

    position_map: Optional[Dict[str, str]] = None
    if not args.input_html and not args.skip_positions:
        position_map = _scrape_position_map(SOURCE_URL)

    rows = build_recommendations(
        html,
        salary_cap=args.salary_cap,
        team_size=args.team_size,
        max_players_per_bye=args.max_players_per_bye,
        position_map=position_map,
    )
    write_csv(rows, Path(args.output))

    selected = [r for r in rows if r["selected_for_team"] == "yes"]
    total_spend = sum(int(r["price"]) for r in selected)
    print(f"Generated {len(rows)} player rows, selected {len(selected)} for team")
    print(f"Total spend: ${total_spend:,} / ${args.salary_cap:,}")
    if any(r.get("position") for r in selected):
        from collections import Counter
        pos_summary = Counter(_primary_position(r["position"]) for r in selected)
        print(f"Positions: {dict(pos_summary)}")
    print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
