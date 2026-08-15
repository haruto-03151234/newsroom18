import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from scripts.news_pipeline.models import Candidate, StoryDraft
from scripts.news_pipeline.publisher import build_edition, publish_edition
from scripts.news_pipeline.time_windows import JST, coverage_window


class PublisherTests(unittest.TestCase):
    def test_static_artifacts_are_generated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "templates").mkdir()
            (root / "templates" / "article.md.tmpl").write_text(
                "# {title}\n{generated_at}\n{coverage}\n{lead}\n{sections}\n{generation_mode}\n",
                encoding="utf-8",
            )
            candidate = Candidate(
                id="a",
                title="見出し",
                description="概要",
                url="https://example.com/news",
                source_name="一次通信",
                category="国内",
                published_at=datetime(2026, 8, 15, 1, tzinfo=UTC),
                primary_source=True,
            )
            draft = StoryDraft(
                candidate_ids=["a"],
                title="<script>重要ニュース</script>",
                dek="要点",
                summary="安全なまとめ",
                why_it_matters="注目点",
                category="国内",
                importance=5,
                tags=["政策"],
            )
            window = coverage_window(datetime(2026, 8, 15, 12, 10, tzinfo=JST), "12")
            edition = build_edition(
                window,
                [draft],
                [candidate],
                datetime(2026, 8, 15, 12, 10, tzinfo=JST),
                "fallback",
                [],
                {"name": "テスト", "tagline": "確かに", "baseUrl": "https://example.com/news/"},
            )
            publish_edition(root, edition, root / "templates" / "article.md.tmpl")
            latest = json.loads((root / "site" / "data" / "latest.json").read_text())
            self.assertEqual(latest["articles"][0]["title"], "重要ニュース")
            self.assertEqual(latest["articles"][0]["importance"], 5)
            self.assertTrue((root / "content" / "2026-08-15-12.md").exists())
            ET.parse(root / "site" / "feed.xml")
            ET.parse(root / "site" / "sitemap.xml")


if __name__ == "__main__":
    unittest.main()
