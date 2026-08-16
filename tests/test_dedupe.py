import unittest
from datetime import UTC, datetime

from scripts.news_pipeline.dedupe import filter_seen, remember_articles
from scripts.news_pipeline.models import Candidate


def candidate(title: str, url: str, *, primary_source: bool = False) -> Candidate:
    return Candidate(
        id=url.rsplit("/", 1)[-1],
        title=title,
        description="",
        url=url,
        source_name="テスト通信",
        category="国内",
        published_at=datetime(2026, 8, 15, 2, tzinfo=UTC),
        primary_source=primary_source,
    )


class DedupeTests(unittest.TestCase):
    def test_exact_url_is_not_republished(self):
        state = {
            "stories": [
                {
                    "title": "政府が新制度を発表",
                    "publishedAt": "2026-08-15T10:00:00+09:00",
                    "urls": ["https://example.com/a"],
                }
            ]
        }
        self.assertEqual(filter_seen([candidate("別タイトル", "https://example.com/a")], state), [])

    def test_near_duplicate_within_36_hours_is_filtered(self):
        state = {
            "stories": [
                {
                    "title": "政府新制度発表",
                    "publishedAt": "2026-08-15T10:00:00+09:00",
                    "urls": ["https://example.com/a"],
                }
            ]
        }
        result = filter_seen([candidate("政府が新制度を発表", "https://other.example/b")], state)
        self.assertEqual(result, [])

    def test_primary_update_with_new_url_survives_similar_title(self):
        state = {
            "stories": [
                {
                    "title": "東京都気象警報・注意報",
                    "publishedAt": "2026-08-15T10:00:00+09:00",
                    "urls": ["https://data.jma.go.jp/old.xml"],
                }
            ]
        }
        update = candidate(
            "東京都気象警報・注意報",
            "https://data.jma.go.jp/new.xml",
            primary_source=True,
        )

        self.assertEqual(filter_seen([update], state), [update])

    def test_primary_update_with_exact_seen_url_is_still_filtered(self):
        url = "https://data.jma.go.jp/same.xml"
        state = {
            "stories": [
                {
                    "title": "東京都気象警報・注意報",
                    "publishedAt": "2026-08-15T10:00:00+09:00",
                    "urls": [url],
                }
            ]
        }
        update = candidate(
            "東京都気象警報・注意報",
            url,
            primary_source=True,
        )

        self.assertEqual(filter_seen([update], state), [])

    def test_remember_uses_only_code_assembled_sources(self):
        state = {"stories": []}
        remember_articles(
            state,
            [
                {
                    "title": "見出し",
                    "publishedAt": "2026-08-15T10:00:00+09:00",
                    "sources": [{"url": "https://example.com/a"}],
                }
            ],
            "2026-08-15-12",
        )
        self.assertEqual(state["stories"][0]["editionId"], "2026-08-15-12")


if __name__ == "__main__":
    unittest.main()
