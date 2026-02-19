import unittest
from pathlib import Path
import tempfile
import csv

from generate_supercoach_csv import build_recommendations, write_csv, generate_team_graphic


SAMPLE_HTML = """
<table>
  <tr><th>Player</th><th>Team</th><th>Position</th><th>Price</th><th>2025 Avg</th></tr>
  <tr><td>Rookie Star</td><td>ADE</td><td>MID</td><td>$170,000</td><td>55.0</td></tr>
  <tr><td>Young Gun</td><td>COLL</td><td>FWD</td><td>$350,000</td><td>78.0</td></tr>
  <tr><td>Premium Pro</td><td>BL</td><td>DEF</td><td>$620,000</td><td>115.0</td></tr>
</table>
"""

SAMPLE_HTML_CURRENT_HEADER = """
<table>
  <tr><th>Player</th><th>Current</th><th>Expected Price</th></tr>
  <tr><td>Main Table Player</td><td>$200,000</td><td>$220,000</td></tr>
</table>
"""

SAMPLE_HTML_TEAM_SELECTION = """
<table>
  <tr><th>Player</th><th>Team</th><th>Position</th><th>Price</th><th>2025 Avg</th><th>Bye</th></tr>
  <tr><td>Alpha Mid</td><td>ADE</td><td>MID</td><td>$300,000</td><td>100.0</td><td>12</td></tr>
  <tr><td>Beta Mid</td><td>BL</td><td>MID</td><td>$250,000</td><td>95.0</td><td>12</td></tr>
  <tr><td>Gamma Def</td><td>COLL</td><td>DEF</td><td>$200,000</td><td>90.0</td><td>13</td></tr>
</table>
"""

SAMPLE_HTML_SALARY_CAP_FULL_TEAM = """
<table>
  <tr><th>Player</th><th>Team</th><th>Position</th><th>Price</th><th>2025 Avg</th></tr>
  <tr><td>Expensive Star</td><td>ADE</td><td>MID</td><td>$400,000</td><td>120.0</td></tr>
  <tr><td>Value One</td><td>BL</td><td>MID</td><td>$200,000</td><td>80.0</td></tr>
  <tr><td>Value Two</td><td>COLL</td><td>DEF</td><td>$200,000</td><td>80.0</td></tr>
  <tr><td>Value Three</td><td>ESS</td><td>FWD</td><td>$200,000</td><td>80.0</td></tr>
</table>
"""

SAMPLE_HTML_NEWS_SIGNALS = """
<table>
  <tr><th>Player</th><th>Team</th><th>Position</th><th>Price</th><th>2025 Avg</th></tr>
  <tr><td>Joshua Kelly</td><td>GWS</td><td>MID</td><td>$477,400</td><td>88.4</td></tr>
  <tr><td>Jagga Smith</td><td>CAR</td><td>MID</td><td>$119,900</td><td>22.0</td></tr>
  <tr><td>Keidean Coleman</td><td>BL</td><td>DEF</td><td>$233,800</td><td>48.0</td></tr>
</table>
"""

SAMPLE_HTML_ROWELL_AND_RUCKS = """
<table>
  <tr><th>Player</th><th>Team</th><th>Position</th><th>Price</th><th>2025 Avg</th></tr>
  <tr><td>Matt Rowell</td><td>GCS</td><td>MID</td><td>$641,400</td><td>118.78</td></tr>
  <tr><td>Tristan Xerri</td><td>NTH</td><td>RUC</td><td>$687,300</td><td>127.28</td></tr>
  <tr><td>Brodie Grundy</td><td>SYD</td><td>RUC</td><td>$677,900</td><td>125.54</td></tr>
  <tr><td>Max Gawn</td><td>MEL</td><td>RUC</td><td>$689,600</td><td>127.70</td></tr>
</table>
"""


class BuildRecommendationsTest(unittest.TestCase):
    def test_growth_factor_prefers_rookie_and_young_development(self):
        rows = build_recommendations(SAMPLE_HTML)
        by_player = {row["player"]: row for row in rows}

        self.assertEqual(by_player["Rookie Star"]["growth_factor"], "1.25")
        self.assertEqual(by_player["Young Gun"]["growth_factor"], "1.10")
        self.assertEqual(by_player["Premium Pro"]["growth_factor"], "1.03")

    def test_supports_main_table_current_price_header(self):
        rows = build_recommendations(SAMPLE_HTML_CURRENT_HEADER)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["player"], "Main Table Player")
        self.assertEqual(rows[0]["price"], "200000")

    def test_selects_team_with_salary_cap_and_bye_limit(self):
        rows = build_recommendations(
            SAMPLE_HTML_TEAM_SELECTION, salary_cap=500000, team_size=2, max_players_per_bye=1
        )
        selected = [row for row in rows if row["selected_for_team"] == "yes"]
        self.assertEqual({row["player"] for row in selected}, {"Alpha Mid", "Gamma Def"})
        self.assertEqual(sum(int(row["price"]) for row in selected), 500000)
        self.assertEqual(sum(1 for row in selected if row["bye_round"] == "12"), 1)
        self.assertEqual(sum(1 for row in selected if row["bye_round"] == "13"), 1)
        self.assertEqual(sum(1 for row in selected if row["is_overall_winner"] == "yes"), 1)

    def test_default_team_size_is_30_players(self):
        player_rows = "".join(
            f"<tr><td>Player {i}</td><td>ADE</td><td>MID</td><td>$100,000</td><td>50.0</td></tr>"
            for i in range(1, 32)
        )
        html = (
            "<table>"
            "<tr><th>Player</th><th>Team</th><th>Position</th><th>Price</th><th>2025 Avg</th></tr>"
            f"{player_rows}"
            "</table>"
        )
        rows = build_recommendations(html)
        selected = [row for row in rows if row["selected_for_team"] == "yes"]
        self.assertEqual(len(selected), 30)

    def test_csv_output_contains_selected_players_only(self):
        rows = build_recommendations(
            SAMPLE_HTML_TEAM_SELECTION, salary_cap=500000, team_size=2, max_players_per_bye=1
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.csv"
            write_csv(rows, output_path)
            with output_path.open(newline="", encoding="utf-8") as handle:
                output_rows = list(csv.DictReader(handle))

        self.assertEqual(len(output_rows), 2)
        self.assertTrue(all(row["selected_for_team"] == "yes" for row in output_rows))

    def test_salary_cap_selection_fills_team_size(self):
        rows = build_recommendations(SAMPLE_HTML_SALARY_CAP_FULL_TEAM, salary_cap=600000, team_size=3)
        selected = [row for row in rows if row["selected_for_team"] == "yes"]
        self.assertEqual(len(selected), 3)
        self.assertLessEqual(sum(int(row["price"]) for row in selected), 600000)
        self.assertNotIn("Expensive Star", {row["player"] for row in selected})

    def test_applies_news_adjustments_for_injury_breakout_and_comeback(self):
        rows = build_recommendations(SAMPLE_HTML_NEWS_SIGNALS, team_size=2, salary_cap=600000)
        by_player = {row["player"]: row for row in rows}
        self.assertEqual(by_player["Joshua Kelly"]["growth_factor"], "0.00")
        self.assertEqual(by_player["Joshua Kelly"]["selected_for_team"], "no")
        self.assertEqual(by_player["Jagga Smith"]["growth_factor"], "1.35")
        self.assertEqual(by_player["Jagga Smith"]["selected_for_team"], "yes")
        self.assertEqual(by_player["Keidean Coleman"]["growth_factor"], "1.20")

    def test_adds_jagga_smith_as_breakout_lock_when_missing(self):
        other_rows = "".join(
            f"<tr><td>Other {i}</td><td>ADE</td><td>MID</td><td>$300,000</td><td>80.0</td></tr>"
            for i in range(1, 102)
        )
        html = (
            "<table>"
            "<tr><th>Player</th><th>Team</th><th>Position</th><th>Price</th><th>2025 Avg</th></tr>"
            "<tr><td>Joshua Kelly</td><td>GWS</td><td>MID</td><td>$477,400</td><td>88.4</td></tr>"
            f"{other_rows}"
            "</table>"
        )
        rows = build_recommendations(html, team_size=1, salary_cap=130000)
        by_player = {row["player"]: row for row in rows}
        self.assertIn("Jagga Smith", by_player)
        self.assertEqual(by_player["Jagga Smith"]["selected_for_team"], "yes")

    def test_footywire_parser_extracts_player_name_and_team(self):
        from generate_supercoach_csv import _parse_footywire_players

        html = """
        <table>
        <tr class="darkcolor" id="rowpid_3921">
        <td height="24" align="left" nowrap>
         <span class="hiddenspan" id="cellpid_3921">Marcus Bontempelli</span>
         <a href="pu-western-bulldogs--marcus-bontempelli" id="cellapid_3921">M Bontempelli</a>
         <span class="hiddenspan" id="celltid_3921">Bulldogs</span>
        </td>
        <td align="center">$706,800</td>
        <td align="center">+$0</td>
        <td align="center">?</td>
        <td align="center">+$0</td>
        <td align="center">$706,800</td>
        <td align="center">+$0</td>
        <td align="center">$706,800</td>
        <td align="center">+$0</td>
        <td align="center">$706,800</td>
        <td align="center">+$0</td>
        </tr>
        </table>
        """
        players = _parse_footywire_players(html)
        self.assertEqual(len(players), 1)
        self.assertEqual(players[0]["player"], "Marcus Bontempelli")
        self.assertEqual(players[0]["team"], "Bulldogs")
        self.assertEqual(players[0]["current"], "$706,800")

    def test_footywire_parser_multiple_players(self):
        from generate_supercoach_csv import _parse_footywire_players

        html = """
        <table>
        <tr class="darkcolor" id="rowpid_100">
        <td><span class="hiddenspan" id="cellpid_100">Zak Butters</span>
        <a href="pu-power--zak-butters">Z Butters</a>
        <span class="hiddenspan" id="celltid_100">Power</span></td>
        <td>$654,800</td><td>+$0</td><td>?</td><td>+$0</td>
        <td>$654,800</td><td>+$0</td><td>$654,800</td><td>+$0</td>
        <td>$676,300</td><td>+$21,500</td>
        </tr>
        <tr class="lightcolor" id="rowpid_101">
        <td><span class="hiddenspan" id="cellpid_101">Nick Daicos</span>
        <a href="pu-magpies--nick-daicos">N Daicos</a>
        <span class="hiddenspan" id="celltid_101">Magpies</span></td>
        <td>$628,400</td><td>+$0</td><td>?</td><td>+$0</td>
        <td>$628,400</td><td>+$0</td><td>$628,400</td><td>+$0</td>
        <td>$628,400</td><td>+$0</td>
        </tr>
        </table>
        """
        players = _parse_footywire_players(html)
        self.assertEqual(len(players), 2)
        self.assertEqual(players[0]["player"], "Zak Butters")
        self.assertEqual(players[1]["player"], "Nick Daicos")

    def test_footywire_build_recommendations_uses_correct_names(self):
        html = """
        <table>
        <tr class="darkcolor" id="rowpid_100">
        <td><span class="hiddenspan" id="cellpid_100">Test Player</span>
        <a href="pu-team--test-player">T Player</a>
        <span class="hiddenspan" id="celltid_100">Bulldogs</span></td>
        <td>$300,000</td><td>+$0</td><td>?</td><td>+$0</td>
        <td>$300,000</td><td>+$0</td><td>$300,000</td><td>+$0</td>
        <td>$300,000</td><td>+$0</td>
        </tr>
        </table>
        """
        rows = build_recommendations(html, team_size=1, salary_cap=500000)
        selected = [r for r in rows if r["selected_for_team"] == "yes"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["player"], "Test Player")
        self.assertEqual(selected[0]["team"], "WBD")

    def test_team_abbreviation_mapping(self):
        from generate_supercoach_csv import _team_abbrev

        self.assertEqual(_team_abbrev("Bulldogs"), "WBD")
        self.assertEqual(_team_abbrev("Demons"), "MEL")
        self.assertEqual(_team_abbrev("Power"), "PTA")
        self.assertEqual(_team_abbrev("Magpies"), "COL")
        self.assertEqual(_team_abbrev("Saints"), "STK")

    def test_position_limits_enforced_with_diverse_positions(self):
        rows_html = []
        for i in range(12):
            rows_html.append(
                f"<tr><td>Def {i}</td><td>ADE</td><td>DEF</td>"
                f"<td>$300,000</td><td>80.0</td></tr>"
            )
        for i in range(12):
            rows_html.append(
                f"<tr><td>Mid {i}</td><td>BL</td><td>MID</td>"
                f"<td>$300,000</td><td>80.0</td></tr>"
            )
        for i in range(12):
            rows_html.append(
                f"<tr><td>Fwd {i}</td><td>COLL</td><td>FWD</td>"
                f"<td>$300,000</td><td>80.0</td></tr>"
            )
        for i in range(6):
            rows_html.append(
                f"<tr><td>Ruc {i}</td><td>ESS</td><td>RUC</td>"
                f"<td>$300,000</td><td>80.0</td></tr>"
            )
        html = (
            "<table>"
            "<tr><th>Player</th><th>Team</th><th>Position</th><th>Price</th><th>2025 Avg</th></tr>"
            + "".join(rows_html)
            + "</table>"
        )
        rows = build_recommendations(html, team_size=30, salary_cap=50_000_000)
        selected = [r for r in rows if r["selected_for_team"] == "yes"]
        self.assertEqual(len(selected), 30)
        from collections import Counter
        pos_counts = Counter(r["position"] for r in selected)
        self.assertLessEqual(pos_counts.get("DEF", 0), 9)
        self.assertLessEqual(pos_counts.get("MID", 0), 11)
        self.assertLessEqual(pos_counts.get("FWD", 0), 10)
        self.assertLessEqual(pos_counts.get("RUC", 0), 4)

    def test_matt_rowell_injury_reduces_growth_factor(self):
        rows = build_recommendations(SAMPLE_HTML_ROWELL_AND_RUCKS, team_size=4, salary_cap=5_000_000)
        by_player = {row["player"]: row for row in rows}
        self.assertEqual(by_player["Matt Rowell"]["growth_factor"], "0.85")

    def test_ruck_rule_changes_reduce_stoppage_dependent_ruck_scoring(self):
        rows = build_recommendations(SAMPLE_HTML_ROWELL_AND_RUCKS, team_size=4, salary_cap=5_000_000)
        by_player = {row["player"]: row for row in rows}
        self.assertEqual(by_player["Tristan Xerri"]["growth_factor"], "0.93")
        self.assertEqual(by_player["Brodie Grundy"]["growth_factor"], "0.97")
        self.assertEqual(by_player["Max Gawn"]["growth_factor"], "0.97")

    def test_ruck_position_limit_reduced_under_new_rules(self):
        from generate_supercoach_csv import POSITION_LIMITS
        self.assertEqual(POSITION_LIMITS["RUC"], 3)

    def test_expected_price_3_influences_growth(self):
        html = """
        <table>
        <tr class="darkcolor" id="rowpid_200">
        <td><span class="hiddenspan" id="cellpid_200">Rising Star</span>
        <a href="pu-team--rising-star">R Star</a>
        <span class="hiddenspan" id="celltid_200">Lions</span></td>
        <td>$300,000</td><td>+$0</td><td>?</td><td>+$0</td>
        <td>$300,000</td><td>+$0</td><td>$300,000</td><td>+$0</td>
        <td>$360,000</td><td>+$60,000</td>
        </tr>
        </table>
        """
        rows = build_recommendations(html, team_size=1, salary_cap=500000)
        player = rows[0]
        factor = float(player["growth_factor"])
        self.assertGreater(factor, 1.0)

    def test_parse_sc_averages_extracts_yearly_data(self):
        from generate_supercoach_csv import _parse_sc_averages
        html = '''
        <table>
        <tr><td class="tbtitle">Past Supercoach Average Scores</td></tr>
        <tr><td><table>
        <tr><td class="bnorm">Year</td><td class="bnorm">Games</td><td class="bnorm">Average</td></tr>
        <tr class="darkcolor"><td><a rel="nofollow" href="...">2022</a></td><td>21</td><td>116.1</td></tr>
        <tr class="lightcolor"><td><a rel="nofollow" href="...">2023</a></td><td>23</td><td>129.7</td></tr>
        <tr class="darkcolor"><td><a rel="nofollow" href="...">2024</a></td><td>23</td><td>126.4</td></tr>
        <tr class="lightcolor"><td><a rel="nofollow" href="...">2025</a></td><td>18</td><td>130.6</td></tr>
        </table></td></tr>
        </table>
        '''
        avgs = _parse_sc_averages(html)
        self.assertEqual(avgs[2022], 116.1)
        self.assertEqual(avgs[2023], 129.7)
        self.assertEqual(avgs[2024], 126.4)
        self.assertEqual(avgs[2025], 130.6)

    def test_compute_sc_trend_upward(self):
        from generate_supercoach_csv import _compute_sc_trend
        avgs = {2022: 80.0, 2023: 90.0, 2024: 100.0}
        trend = _compute_sc_trend(avgs)
        self.assertGreater(trend, 1.05)

    def test_compute_sc_trend_peaking(self):
        from generate_supercoach_csv import _compute_sc_trend
        avgs = {2022: 120.0, 2023: 129.7, 2024: 126.4, 2025: 130.6}
        trend = _compute_sc_trend(avgs)
        self.assertGreaterEqual(trend, 1.0)

    def test_compute_sc_trend_declining(self):
        from generate_supercoach_csv import _compute_sc_trend
        avgs = {2022: 120.0, 2023: 110.0, 2024: 95.0}
        trend = _compute_sc_trend(avgs)
        self.assertLess(trend, 1.0)

    def test_compute_sc_trend_insufficient_data(self):
        from generate_supercoach_csv import _compute_sc_trend
        avgs = {2024: 100.0}
        trend = _compute_sc_trend(avgs)
        self.assertEqual(trend, 1.0)

    def test_sc_trend_applied_to_recommendations(self):
        html = """
        <table>
          <tr><th>Player</th><th>Team</th><th>Position</th><th>Price</th><th>2025 Avg</th></tr>
          <tr><td>Trending Up</td><td>ADE</td><td>MID</td><td>$400,000</td><td>100.0</td></tr>
          <tr><td>Declining Star</td><td>BL</td><td>MID</td><td>$400,000</td><td>100.0</td></tr>
        </table>
        """
        sc_profiles = {
            "Trending Up": {2022: 70.0, 2023: 85.0, 2024: 100.0},
            "Declining Star": {2022: 120.0, 2023: 110.0, 2024: 95.0},
        }
        rows = build_recommendations(html, team_size=2, salary_cap=1_000_000, sc_profiles=sc_profiles)
        by_player = {r["player"]: r for r in rows}
        trending_factor = float(by_player["Trending Up"]["growth_factor"])
        declining_factor = float(by_player["Declining Star"]["growth_factor"])
        self.assertGreater(trending_factor, declining_factor)

    def test_footywire_parser_captures_profile_link(self):
        from generate_supercoach_csv import _parse_footywire_players
        html = '''
        <table>
        <tr class="darkcolor" id="rowpid_3921">
        <td height="24" align="left" nowrap>
         <span class="hiddenspan" id="cellpid_3921">Marcus Bontempelli</span>
         <a href="pu-western-bulldogs--marcus-bontempelli" id="cellapid_3921">M Bontempelli</a>
         <span class="hiddenspan" id="celltid_3921">Bulldogs</span>
        </td>
        <td align="center">$706,800</td>
        <td align="center">+$0</td>
        <td align="center">?</td>
        <td align="center">+$0</td>
        <td align="center">$706,800</td>
        <td align="center">+$0</td>
        <td align="center">$706,800</td>
        <td align="center">+$0</td>
        <td align="center">$706,800</td>
        <td align="center">+$0</td>
        </tr>
        </table>
        '''
        players = _parse_footywire_players(html)
        self.assertEqual(len(players), 1)
        self.assertEqual(players[0]["profile_link"], "pu-western-bulldogs--marcus-bontempelli")

    def test_csv_output_sorted_by_position(self):
        html = """
        <table>
          <tr><th>Player</th><th>Team</th><th>Position</th><th>Price</th><th>2025 Avg</th></tr>
          <tr><td>Mid Player</td><td>ADE</td><td>MID</td><td>$300,000</td><td>90.0</td></tr>
          <tr><td>Def Player</td><td>BL</td><td>DEF</td><td>$300,000</td><td>90.0</td></tr>
          <tr><td>Fwd Player</td><td>COLL</td><td>FWD</td><td>$300,000</td><td>90.0</td></tr>
        </table>
        """
        rows = build_recommendations(html, salary_cap=1_000_000, team_size=3)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.csv"
            write_csv(rows, output_path)
            with output_path.open(newline="", encoding="utf-8") as handle:
                output_rows = list(csv.DictReader(handle))
        positions = [r["position"] for r in output_rows]
        self.assertEqual(positions, ["DEF", "MID", "FWD"])

    def test_generate_team_graphic_creates_svg(self):
        html = """
        <table>
          <tr><th>Player</th><th>Team</th><th>Position</th><th>Price</th><th>2025 Avg</th></tr>
          <tr><td>Def Player</td><td>ADE</td><td>DEF</td><td>$300,000</td><td>90.0</td></tr>
          <tr><td>Mid Player</td><td>BL</td><td>MID</td><td>$300,000</td><td>90.0</td></tr>
          <tr><td>Fwd Player</td><td>COLL</td><td>FWD</td><td>$300,000</td><td>90.0</td></tr>
        </table>
        """
        rows = build_recommendations(html, salary_cap=1_000_000, team_size=3)
        with tempfile.TemporaryDirectory() as tmpdir:
            svg_path = Path(tmpdir) / "team.svg"
            generate_team_graphic(rows, svg_path)
            self.assertTrue(svg_path.exists())
            content = svg_path.read_text(encoding="utf-8")
            self.assertIn("<svg", content)
            self.assertIn("Def Player", content)
            self.assertIn("Mid Player", content)
            self.assertIn("Fwd Player", content)
            self.assertIn("DEF", content)
            self.assertIn("MID", content)
            self.assertIn("FWD", content)

    def test_generate_team_graphic_empty_team(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            svg_path = Path(tmpdir) / "team.svg"
            generate_team_graphic([], svg_path)
            self.assertFalse(svg_path.exists())


if __name__ == "__main__":
    unittest.main()
