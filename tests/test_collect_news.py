import unittest
import sys
import types
import json
import tempfile
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

if "feedparser" not in sys.modules:
    try:
        __import__("feedparser")
    except ModuleNotFoundError:
        stub = types.ModuleType("feedparser")
        stub.parse = lambda _payload: types.SimpleNamespace(
            bozo=False,
            bozo_exception=None,
            entries=[],
        )
        sys.modules["feedparser"] = stub

from scripts.collect_news import (
    _assert_publishable_source_mix,
    _record_completion,
    _select_fresh_candidates,
    main,
)
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


def edition_with_sources(sources):
    return {
        "edition": {"id": "2026-08-15-12"},
        "articles": [
            {
                "title": "見出し",
                "publishedAt": "2026-08-15T11:00:00+09:00",
                "sources": sources,
            }
        ],
    }


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


class SourceMixGateTests(unittest.TestCase):
    def test_rejects_one_non_primary_publisher(self):
        edition = edition_with_sources(
            [{"publisherId": "nhk", "name": "NHK ONE", "isPrimary": False}]
        )

        with self.assertRaises(RuntimeError):
            _assert_publishable_source_mix(edition)

    def test_allows_one_primary_publisher(self):
        edition = edition_with_sources(
            [{"publisherId": "jma", "name": "気象庁", "isPrimary": True}]
        )

        _assert_publishable_source_mix(edition)

    def test_allows_two_non_primary_publishers(self):
        edition = edition_with_sources(
            [
                {"publisherId": "nhk", "name": "NHK ONE", "isPrimary": False},
                {"publisherId": "mainichi", "name": "毎日新聞", "isPrimary": False},
            ]
        )

        _assert_publishable_source_mix(edition)

    def test_rejects_empty_edition(self):
        with self.assertRaises(RuntimeError):
            _assert_publishable_source_mix(
                {"edition": {"id": "2026-08-15-12"}, "articles": []}
            )

    def test_invalid_edition_does_not_publish_or_write_state(self):
        candidate = make_candidate("nhk-only", "政府が新制度を発表")
        invalid = edition_with_sources(
            [{"publisherId": "nhk", "name": "NHK ONE", "isPrimary": False}]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config" / "site.json").write_text("{}", encoding="utf-8")
            state_path = root / ".state" / "news-state.json"
            state_path.parent.mkdir()
            original_state = {
                "version": 1,
                "lastCompletedEnd": None,
                "stories": [],
                "sentinel": "unchanged",
            }
            state_path.write_text(
                json.dumps(original_state, ensure_ascii=False), encoding="utf-8"
            )
            args = Namespace(
                root=root,
                now="2026-08-15T12:10:00+09:00",
                edition="12",
                force=True,
                catchup_limit=1,
            )

            with patch("scripts.collect_news.parse_args", return_value=args), patch(
                "scripts.collect_news.load_feed_config", return_value=[{"id": "test"}]
            ), patch(
                "scripts.collect_news.collect_candidates",
                return_value=([candidate], []),
            ), patch(
                "scripts.collect_news.create_drafts",
                return_value=([], "structured"),
            ), patch(
                "scripts.collect_news.build_edition", return_value=invalid
            ), patch("scripts.collect_news.publish_edition") as publish:
                with self.assertRaises(RuntimeError):
                    main()

            publish.assert_not_called()
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")), original_state
            )

    def test_later_invalid_catchup_prevents_all_publication(self):
        candidate = make_candidate("candidate", "政府が新制度を発表")
        valid = edition_with_sources(
            [{"publisherId": "jma", "name": "気象庁", "isPrimary": True}]
        )
        invalid = edition_with_sources(
            [{"publisherId": "nhk", "name": "NHK ONE", "isPrimary": False}]
        )
        noon = coverage_window(datetime(2026, 8, 15, 12, 10, tzinfo=JST), "12")
        evening = coverage_window(
            datetime(2026, 8, 15, 18, 10, tzinfo=JST), "18"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config" / "site.json").write_text("{}", encoding="utf-8")
            args = Namespace(
                root=root,
                now="2026-08-15T18:10:00+09:00",
                edition="18",
                force=False,
                catchup_limit=2,
            )

            with patch("scripts.collect_news.parse_args", return_value=args), patch(
                "scripts.collect_news.load_feed_config", return_value=[{"id": "test"}]
            ), patch(
                "scripts.collect_news.missing_windows", return_value=[noon, evening]
            ), patch(
                "scripts.collect_news.collect_candidates",
                return_value=([candidate], []),
            ), patch(
                "scripts.collect_news.create_drafts",
                return_value=([], "structured"),
            ), patch(
                "scripts.collect_news.build_edition", side_effect=[valid, invalid]
            ), patch("scripts.collect_news.publish_edition") as publish:
                with self.assertRaises(RuntimeError):
                    main()

            publish.assert_not_called()
            self.assertFalse((root / ".state" / "news-state.json").exists())


if __name__ == "__main__":
    unittest.main()
