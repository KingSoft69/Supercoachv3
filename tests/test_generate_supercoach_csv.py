import unittest

from generate_supercoach_csv import build_recommendations


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


if __name__ == "__main__":
    unittest.main()
