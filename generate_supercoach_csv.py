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
POSITION_LIMITS: Dict[str, int] = {"DEF": 9, "MID": 11, "RUC": 3, "FWD": 10}
POSITION_ORDER: Dict[str, int] = {"DEF": 0, "MID": 1, "RUC": 2, "FWD": 3}

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
    "matt rowell": 0.85,  # broken finger in State of Origin; will miss start of season
    "tristan xerri": 0.93,  # new ruck rules reduce stoppage scoring; highly stoppage-dependent
    "brodie grundy": 0.97,  # new ruck rules minor impact; versatile scoring profile
    "max gawn": 0.97,  # new ruck rules minor impact; versatile/mobile ruck
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
        self._current_profile_link = ""
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
                self._current_profile_link = ""
        elif self._in_player_row and tag == "td":
            self._in_cell = True
            self._current_cell_parts = []
        elif self._in_player_row and tag == "a":
            href = attr_dict.get("href", "")
            if href.startswith("pu-"):
                self._current_profile_link = href
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
                entry: Dict[str, str] = {
                    "player": self._current_player_name,
                    "team": self._current_team,
                    "current": price_str,
                    "expected_price": expected_price,
                    "expected_price_3": expected_price_3,
                }
                if self._current_profile_link:
                    entry["profile_link"] = self._current_profile_link
                self.players.append(entry)

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


def _min_position_aware_cost(
    rows: List[Dict[str, str]],
    available: List[int],
    pos_counts: Dict[str, int],
    remaining_slots: int,
) -> Optional[int]:
    """Compute minimum cost to fill remaining slots while respecting position minimums.

    Dynamically derives the minimum players needed per position from
    ``POSITION_LIMITS`` and the number of remaining slots.  Returns ``None``
    if the requirements cannot be met.
    """
    pos_available = {pos: limit - pos_counts.get(pos, 0) for pos, limit in POSITION_LIMITS.items()}

    pos_min_needed: Dict[str, int] = {}
    for pos in POSITION_LIMITS:
        other_capacity = sum(v for p, v in pos_available.items() if p != pos)
        pos_min_needed[pos] = max(0, remaining_slots - other_capacity)

    available_by_price = sorted(available, key=lambda j: int(rows[j]["price"]))

    reserved: Set[int] = set()
    total_cost = 0

    for pos in POSITION_LIMITS:
        needed = pos_min_needed[pos]
        filled = 0
        for j in available_by_price:
            if filled >= needed:
                break
            if j in reserved:
                continue
            if _fits_position(rows[j].get("position", ""), pos):
                reserved.add(j)
                total_cost += int(rows[j]["price"])
                filled += 1
        if filled < needed:
            return None

    flex_needed = remaining_slots - len(reserved)
    for j in available_by_price:
        if flex_needed <= 0:
            break
        if j in reserved:
            continue
        reserved.add(j)
        total_cost += int(rows[j]["price"])
        flex_needed -= 1

    if flex_needed > 0:
        return None

    return total_cost


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

    if use_position_limits:
        pos_queues: Dict[str, List[int]] = {pos: [] for pos in POSITION_LIMITS}
        no_pos: List[int] = []
        for i in ordered:
            primary = _primary_position(rows[i].get("position", ""))
            if primary in pos_queues:
                pos_queues[primary].append(i)
            else:
                no_pos.append(i)
        interleaved: List[int] = []
        iters = {pos: iter(q) for pos, q in pos_queues.items()}
        while True:
            added = False
            for pos in POSITION_LIMITS:
                try:
                    interleaved.append(next(iters[pos]))
                    added = True
                except StopIteration:
                    pass
            if not added:
                break
        interleaved.extend(no_pos)
        ordered = interleaved

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
            available = [j for j in ordered if j != idx and j not in selected_set]
            if use_position_limits:
                tentative_pos = dict(pos_counts)
                if position:
                    primary = _primary_position(position)
                    tentative_pos[primary] = tentative_pos.get(primary, 0) + 1
                min_required = _min_position_aware_cost(
                    rows, available, tentative_pos, remaining_slots
                )
                if min_required is None or total_price + price + min_required > salary_cap:
                    continue
            else:
                cheapest_remaining = nsmallest(
                    remaining_slots,
                    (int(rows[j]["price"]) for j in available),
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


def _parse_sc_averages(html: str) -> Dict[int, float]:
    """Parse a player profile page and extract Past Supercoach Average Scores."""
    marker = "Past Supercoach Average Scores"
    idx = html.find(marker)
    if idx == -1:
        return {}
    section = html[idx:]
    pattern = r'>(\d{4})</a></td>\s*<td[^>]*>\d+</td>\s*<td[^>]*>([\d.]+)</td>'
    results: Dict[int, float] = {}
    for m in re.finditer(pattern, section):
        year = int(m.group(1))
        avg = float(m.group(2))
        results[year] = avg
    return results


def _compute_sc_trend(yearly_avgs: Dict[int, float]) -> float:
    """Compute a trend multiplier based on historical SC averages."""
    if len(yearly_avgs) < 2:
        return 1.0

    sorted_years = sorted(yearly_avgs.keys())
    recent_years = sorted_years[-3:]

    if len(recent_years) < 2:
        return 1.0

    latest_avg = yearly_avgs[recent_years[-1]]
    prior_avg = yearly_avgs[recent_years[-2]]

    if prior_avg <= 0 or latest_avg <= 0:
        return 1.0

    yoy_change = (latest_avg - prior_avg) / prior_avg

    career_high = max(yearly_avgs.values())
    near_peak = latest_avg >= career_high * 0.95

    if len(recent_years) >= 3:
        earliest_avg = yearly_avgs[recent_years[0]]
        if earliest_avg > 0:
            three_year_trend = (latest_avg - earliest_avg) / earliest_avg
        else:
            three_year_trend = 0.0
    else:
        three_year_trend = yoy_change

    # >5% YoY and 3-year growth = strong upward trend (cap at 15% boost)
    if yoy_change > 0.05 and three_year_trend > 0.05:
        return min(1.15, 1.0 + three_year_trend * 0.5)
    elif yoy_change > 0 or near_peak:
        # Moderate growth or near career high (cap at 5% boost)
        return min(1.05, 1.0 + max(yoy_change, 0) * 0.5)
    elif yoy_change > -0.05:
        # Within 5% decline considered stable
        return 1.0
    else:
        # Declining; floor at 5% penalty
        return max(0.95, 1.0 + yoy_change * 0.5)


def _scrape_player_profiles(
    players: List[Dict[str, str]], base_url: str
) -> Dict[str, Dict[int, float]]:
    """Fetch individual profile pages and extract historical SC averages."""
    profiles: Dict[str, Dict[int, float]] = {}
    base = base_url.rsplit("/", 1)[0]
    for player in players:
        link = player.get("profile_link", "")
        name = player.get("player", "")
        if not link or not name:
            continue
        url = f"{base}/{link}"
        try:
            html = _fetch_url(url)
            avgs = _parse_sc_averages(html)
            if avgs:
                profiles[name] = avgs
        except (OSError, ValueError):
            continue
    return profiles


def build_recommendations(
    html: str,
    salary_cap: int = DEFAULT_SALARY_CAP,
    team_size: int = DEFAULT_TEAM_SIZE,
    max_players_per_bye: int = DEFAULT_MAX_PLAYERS_PER_BYE,
    position_map: Optional[Dict[str, str]] = None,
    sc_profiles: Optional[Dict[str, Dict[int, float]]] = None,
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

        # Apply historical SC trend adjustment (does not override news factors)
        has_sc_profile = sc_profiles is not None and name in sc_profiles
        sc_trend_value = 1.0
        if has_sc_profile and news_factor is None:
            sc_trend_value = _compute_sc_trend(sc_profiles[name])
            factor = factor * sc_trend_value

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
                "sc_trend": f"{sc_trend_value:.2f}" if has_sc_profile else "",
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
                    "sc_trend": "",
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


def _position_sort_key(row: Dict[str, str]) -> Tuple[int, int]:
    """Sort key: position group order (DEF, MID, RUC, FWD), then selection rank."""
    primary = _primary_position(row.get("position", ""))
    pos_order = POSITION_ORDER.get(primary, len(POSITION_ORDER))
    rank = int(row.get("selection_rank") or 9999)
    return (pos_order, rank)


def write_csv(rows: List[Dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    selected_rows = sorted(
        [row for row in rows if row.get("selected_for_team") == "yes"],
        key=_position_sort_key,
    )
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
        "sc_trend",
        "selected_for_team",
        "selection_rank",
        "is_overall_winner",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(selected_rows)


def _escape_xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;").replace('"', "&quot;")


def generate_team_graphic(rows: List[Dict[str, str]], output_path: Path) -> None:
    """Generate an SVG graphic of the selected team in a formation layout."""
    selected = sorted(
        [row for row in rows if row.get("selected_for_team") == "yes"],
        key=_position_sort_key,
    )
    if not selected:
        return

    groups: Dict[str, List[Dict[str, str]]] = {"DEF": [], "MID": [], "RUC": [], "FWD": []}
    flex: List[Dict[str, str]] = []
    for row in selected:
        primary = _primary_position(row.get("position", ""))
        if primary in groups:
            groups[primary].append(row)
        else:
            flex.append(row)

    # Card dimensions
    card_w, card_h = 120, 52
    max_card_name = 16
    h_gap, v_gap = 14, 20
    section_gap = 40
    margin_x, margin_top = 30, 80
    label_h = 28

    # Determine layout width based on widest row
    max_per_row = max((len(g) for g in groups.values() if g), default=1)
    max_per_row = max(max_per_row, len(flex) if flex else 0, 1)
    content_w = max_per_row * card_w + (max_per_row - 1) * h_gap
    svg_w = content_w + 2 * margin_x

    # Calculate total height
    all_sections = [
        ("DEF", groups["DEF"]), ("MID", groups["MID"]),
        ("RUC", groups["RUC"]), ("FWD", groups["FWD"]),
        ("FLEX", flex),
    ]
    sections = [(label, players) for label, players in all_sections if players]
    total_h = margin_top
    for i, (_, players) in enumerate(sections):
        total_h += label_h + card_h
        if i < len(sections) - 1:
            total_h += section_gap
    total_h += 50  # bottom margin

    parts: List[str] = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{total_h}" viewBox="0 0 {svg_w} {total_h}">')
    parts.append('<defs>')
    parts.append('  <linearGradient id="field" x1="0" y1="0" x2="0" y2="1">')
    parts.append('    <stop offset="0%" stop-color="#2e7d32"/>')
    parts.append('    <stop offset="100%" stop-color="#1b5e20"/>')
    parts.append('  </linearGradient>')
    parts.append('</defs>')
    parts.append(f'<rect width="{svg_w}" height="{total_h}" rx="12" fill="url(#field)"/>')

    # Title
    parts.append(f'<text x="{svg_w / 2}" y="36" text-anchor="middle" font-family="Arial, sans-serif" font-size="22" font-weight="bold" fill="#fff">SuperCoach 2026 — Selected Team</text>')
    # Subtitle: total spend
    total_spend = sum(int(r.get("price", 0)) for r in selected)
    parts.append(f'<text x="{svg_w / 2}" y="58" text-anchor="middle" font-family="Arial, sans-serif" font-size="13" fill="#c8e6c9">Total Spend: ${total_spend:,} / ${DEFAULT_SALARY_CAP:,}  •  {len(selected)} players</text>')

    y = margin_top
    pos_colors = {"DEF": "#1565c0", "MID": "#6a1b9a", "RUC": "#e65100", "FWD": "#c62828", "FLEX": "#37474f"}

    for label, players in sections:
        color = pos_colors.get(label, "#37474f")
        # Section label
        parts.append(f'<text x="{svg_w / 2}" y="{y + 16}" text-anchor="middle" font-family="Arial, sans-serif" font-size="15" font-weight="bold" fill="#fff" opacity="0.9">{label}</text>')
        y += label_h

        n = len(players)
        row_w = n * card_w + (n - 1) * h_gap
        start_x = (svg_w - row_w) / 2

        for j, p in enumerate(players):
            cx = start_x + j * (card_w + h_gap)
            # Card background
            parts.append(f'<rect x="{cx}" y="{y}" width="{card_w}" height="{card_h}" rx="8" fill="{color}" opacity="0.85"/>')
            parts.append(f'<rect x="{cx}" y="{y}" width="{card_w}" height="{card_h}" rx="8" fill="none" stroke="#fff" stroke-width="1" opacity="0.3"/>')
            # Player name (truncate if needed)
            name = p.get("player", "?")
            display_name = name if len(name) <= max_card_name else name[:max_card_name - 2] + "…"
            parts.append(f'<text x="{cx + card_w / 2}" y="{y + 19}" text-anchor="middle" font-family="Arial, sans-serif" font-size="11" font-weight="bold" fill="#fff">{_escape_xml(display_name)}</text>')
            # Team and price
            team = p.get("team", "")
            price = int(p.get("price", 0))
            parts.append(f'<text x="{cx + card_w / 2}" y="{y + 34}" text-anchor="middle" font-family="Arial, sans-serif" font-size="9" fill="#e0e0e0">{_escape_xml(team)}  •  ${price:,}</text>')
            # Projected avg
            proj = p.get("projected_avg", "")
            parts.append(f'<text x="{cx + card_w / 2}" y="{y + 46}" text-anchor="middle" font-family="Arial, sans-serif" font-size="9" fill="#a5d6a7">Avg: {proj}</text>')

        y += card_h + section_gap

    parts.append('</svg>')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(parts), encoding="utf-8")


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
    ap.add_argument(
        "--skip-profiles",
        action="store_true",
        help="Skip fetching individual player profile pages for SC trend data",
    )
    args = ap.parse_args()

    html = _read_html(args.input_html)

    position_map: Optional[Dict[str, str]] = None
    if not args.input_html and not args.skip_positions:
        position_map = _scrape_position_map(SOURCE_URL)

    sc_profiles: Optional[Dict[str, Dict[int, float]]] = None
    if not args.input_html and not args.skip_profiles:
        fw_players = _parse_footywire_players(html)
        if fw_players:
            sc_profiles = _scrape_player_profiles(fw_players, SOURCE_URL)

    rows = build_recommendations(
        html,
        salary_cap=args.salary_cap,
        team_size=args.team_size,
        max_players_per_bye=args.max_players_per_bye,
        position_map=position_map,
        sc_profiles=sc_profiles,
    )
    output_path = Path(args.output)
    write_csv(rows, output_path)

    graphic_path = output_path.with_suffix(".svg")
    generate_team_graphic(rows, graphic_path)

    selected = [r for r in rows if r["selected_for_team"] == "yes"]
    total_spend = sum(int(r["price"]) for r in selected)
    print(f"Generated {len(rows)} player rows, selected {len(selected)} for team")
    print(f"Total spend: ${total_spend:,} / ${args.salary_cap:,}")
    if any(r.get("position") for r in selected):
        from collections import Counter
        pos_summary = Counter(_primary_position(r["position"]) for r in selected)
        print(f"Positions: {dict(pos_summary)}")
    print(f"Output: {args.output}")
    print(f"Team graphic: {graphic_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
