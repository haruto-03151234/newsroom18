import unittest
import sys
import types
from datetime import UTC, datetime

if "feedparser" not in sys.modules:
    try:
        __import__("feedparser")
    except ModuleNotFoundError:
        stub = types.ModuleType("feedparser")
        stub.parse = lambda _payload: None
        sys.modules["feedparser"] = stub

from scripts.collect_news import _record_completion, _select_fresh_candidates
from scripts.news_pipeline.models import Candidate
from scripts.news_pipeline.time_windows import JST, coverage_window


def make_candidate(identifier: str, title: str) -> Candidate:
    return Candidate(
        id=identifier,
        title=title,
        description="",
        url=f"https://example.com/{identifier}",
        source_name="テスト通信",
        category="国内",
        published_at=datetime(2026, 8, 15, 2, tzinfo=UTC),
        publisher_id="test",
    )


class CollectNewsForceTests(unittest.TestCase):
    def test_force_replaces_only_target_edition_state(self):
        target = make_candidate("target", "政府が新制度を発表")
        older = make_candidate("older", "スポーツ大会の結果")
        state = {
            "stories": [
                {
                    "editionId": "2026-08-15-12",
                    "title": "政府が新制度を発表",
                    "publishedAt": "2026-08-15T11:00:00+09:00",
                    "urls": [target.url],
                },
                {
                    "editionId": "2026-08-15-06",
                    "title": "スポーツ大会の結果",
                    "publishedAt": "2026-08-15T11:00:00+09:00",
                    "urls": [older.url],
                },
            ]
        }

        fresh = _select_fresh_candidates(
            [target, older], state, "2026-08-15-12", force=True
        )

        self.assertEqual([item.id for item in fresh], ["target"])
        self.assertEqual(
            [story["editionId"] for story in state["stories"]],
            ["2026-08-15-06"],
        )

    def test_past_force_does_not_rewind_completed_window(self):
        state = {
            "lastCompletedEnd": "2026-08-15T18:00:00+09:00",
            "lastCompletedEdition": "2026-08-15-18",
        }
        noon = coverage_window(datetime(2026, 8, 15, 12, 10, tzinfo=JST), "12")

        _record_completion(state, noon)

        self.assertEqual(state["lastCompletedEnd"], "2026-08-15T18:00:00+09:00")
        self.assertEqual(state["lastCompletedEdition"], "2026-08-15-18")


if __name__ == "__main__":
    unittest.main()
