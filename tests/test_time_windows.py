import unittest
from datetime import datetime

from scripts.news_pipeline.time_windows import JST, coverage_window, missing_windows


class CoverageWindowTests(unittest.TestCase):
    def test_morning_window_crosses_midnight(self):
        window = coverage_window(datetime(2026, 8, 15, 6, 40, tzinfo=JST), "06")
        self.assertEqual(window.start.isoformat(), "2026-08-14T18:00:00+09:00")
        self.assertEqual(window.end.isoformat(), "2026-08-15T06:00:00+09:00")

    def test_delayed_evening_run_stays_on_previous_day(self):
        window = coverage_window(datetime(2026, 8, 16, 1, 0, tzinfo=JST), "18")
        self.assertEqual(window.id, "2026-08-15-18")
        self.assertEqual(window.start.hour, 12)

    def test_exact_boundary_belongs_to_new_slot(self):
        window = coverage_window(datetime(2026, 8, 15, 12, 0, tzinfo=JST), "12")
        self.assertEqual(window.start.hour, 6)
        self.assertEqual(window.end.hour, 12)

    def test_auto_before_six_uses_previous_evening(self):
        window = coverage_window(datetime(2026, 8, 15, 1, 0, tzinfo=JST), "auto")
        self.assertEqual(window.id, "2026-08-14-18")

    def test_missing_windows_catch_up(self):
        target = coverage_window(datetime(2026, 8, 15, 18, 10, tzinfo=JST), "18")
        windows = missing_windows("2026-08-15T06:00:00+09:00", target)
        self.assertEqual([item.edition for item in windows], ["12", "18"])

    def test_completed_target_is_noop(self):
        target = coverage_window(datetime(2026, 8, 15, 18, 10, tzinfo=JST), "18")
        self.assertEqual(missing_windows(target.end.isoformat(), target), [])


if __name__ == "__main__":
    unittest.main()

