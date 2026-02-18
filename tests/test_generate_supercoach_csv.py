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


class BuildRecommendationsTest(unittest.TestCase):
    def test_growth_factor_prefers_rookie_and_young_development(self):
        rows = build_recommendations(SAMPLE_HTML)
        by_player = {row["player"]: row for row in rows}

        self.assertEqual(by_player["Rookie Star"]["growth_factor"], "1.25")
        self.assertEqual(by_player["Young Gun"]["growth_factor"], "1.10")
        self.assertEqual(by_player["Premium Pro"]["growth_factor"], "1.03")


if __name__ == "__main__":
    unittest.main()
