from django.test import SimpleTestCase

from .risk import HIGH, LOW, MODERATE, NO_TRAVEL, SEVERE, classify, recommend, summarize
from .services import sample_checkpoints


def level(wind=0, rain=0, snow=0, load=0):
    return classify(wind, rain, snow, load)[0]


class RiskRulesTest(SimpleTestCase):
    def test_wind_bands(self):
        for mph, expected in [(24.9, LOW), (25, MODERATE), (34, MODERATE), (35, HIGH),
                              (44, HIGH), (45, SEVERE), (54, SEVERE), (55, NO_TRAVEL)]:
            self.assertEqual(level(wind=mph), expected, mph)

    def test_rain_bands(self):
        for rate, expected in [(0.09, LOW), (0.10, MODERATE), (0.25, HIGH), (0.50, SEVERE),
                               (1.00, SEVERE), (1.01, NO_TRAVEL)]:
            self.assertEqual(level(rain=rate), expected, rate)

    def test_snow_bands(self):
        for rate, expected in [(0.4, LOW), (0.5, MODERATE), (1.0, HIGH), (2.0, SEVERE),
                               (3.0, SEVERE), (3.1, NO_TRAVEL)]:
            self.assertEqual(level(snow=rate), expected, rate)

    def test_worst_condition_wins(self):
        self.assertEqual(level(wind=26, rain=0.3, snow=0.1), HIGH)

    def test_load_rules(self):
        self.assertEqual(level(wind=60, load=0), NO_TRAVEL)
        self.assertEqual(level(wind=50, load=30_000), SEVERE)
        self.assertEqual(level(wind=50, load=30_001), NO_TRAVEL)
        self.assertEqual(level(wind=40, load=40_000), HIGH)
        self.assertEqual(level(wind=40, load=40_001), SEVERE)
        self.assertEqual(level(wind=40, rain=1.5, load=40_001), NO_TRAVEL)  # never lowers
        self.assertIn("load", classify(40, 0, 0, 45_000)[1][0])


def route(name, levels, hours, step=10):
    cps = [{"mile": i * step, "level": lv} for i, lv in enumerate(levels)]
    return {"name": name, "duration_h": hours, "checkpoints": cps, "summary": summarize(cps)}


class RecommendationTest(SimpleTestCase):
    def test_summary_splits_segments_between_checkpoints(self):
        s = summarize([{"mile": 0, "level": LOW}, {"mile": 10, "level": HIGH}, {"mile": 20, "level": HIGH}])
        self.assertEqual(s["miles_by_level"]["Low"], 5)
        self.assertEqual(s["miles_by_level"]["High"], 15)
        self.assertEqual(s["avg_risk"], 1.5)

    def test_preference_order(self):
        severe = route("A", [LOW, SEVERE, LOW], 5)
        high = route("B", [LOW, HIGH, HIGH, LOW], 9)
        self.assertEqual(recommend([severe, high])[0], 1)  # fewer Severe beats faster
        moderate = route("C", [MODERATE, MODERATE, MODERATE], 9)
        self.assertEqual(recommend([high, moderate])[0], 1)  # fewer High beats lower average
        low_slow, low_fast = route("D", [LOW, LOW], 8), route("E", [LOW, LOW], 6)
        best, reason = recommend([low_slow, low_fast])
        self.assertEqual(best, 1)
        self.assertIn("travel time", reason)

    def test_no_travel_ranks_worst(self):
        blocked = route("A", [LOW, NO_TRAVEL, LOW], 5)
        severe = route("B", [LOW, SEVERE, SEVERE, LOW], 9)
        self.assertEqual(recommend([blocked, severe])[0], 1)


class SamplingTest(SimpleTestCase):
    def test_checkpoints_every_interval_with_eta(self):
        mile = 1609.344
        r = {"coords": [(0, 0), (0, 1), (0, 2)], "seg_m": [30 * mile, 30 * mile], "seg_s": [1800, 3600]}
        cps = sample_checkpoints(r, 25)
        self.assertEqual([round(c["mile"]) for c in cps], [0, 25, 50, 60])
        self.assertAlmostEqual(cps[1]["offset_s"], 1500)  # 25/30 of the first 30 min
        self.assertAlmostEqual(cps[2]["offset_s"], 1800 + 3600 * 20 / 30)
