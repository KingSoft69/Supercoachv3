import unittest
from pathlib import Path
import tempfile
import csv

from generate_supercoach_csv import build_recommendations, write_csv


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

    def test_default_team_size_selects_30_players(self):
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


if __name__ == "__main__":
    unittest.main()
