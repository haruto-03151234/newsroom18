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
            self.assertEqual(latest["articles"][0]["articleType"], "brief")
            self.assertEqual(latest["generationMode"], "structured")
            self.assertEqual(latest["schemaVersion"], 3)
            self.assertTrue(latest["articles"][0]["sources"][0]["isPrimary"])
            self.assertEqual(latest["articles"][0]["sources"][0]["type"], "一次情報")
            self.assertTrue((root / "content" / "2026-08-15-12.md").exists())
            ET.parse(root / "site" / "feed.xml")
            ET.parse(root / "site" / "sitemap.xml")

    def test_structured_fields_use_independent_publishers(self):
        published = datetime(2026, 8, 15, 1, tzinfo=UTC)
        candidates = [
            Candidate(
                id="nhk-top",
                title="地震活動について発表",
                description="",
                url="https://example.com/nhk/top",
                source_name="NHK ONE 主要ニュース",
                category="国内",
                published_at=published,
                priority=5,
                publisher_id="nhk",
            ),
            Candidate(
                id="nhk-world",
                title="地震活動の続報",
                description="",
                url="https://example.com/nhk/world",
                source_name="NHK ONE 国際",
                category="国内",
                published_at=published,
                priority=4,
                publisher_id="nhk",
            ),
        ]
        draft = StoryDraft(
            candidate_ids=["nhk-top", "nhk-world"],
            title="地震活動が続く",
            dek="公表内容を整理しました。",
            summary="関係機関が地震活動について情報を更新しました。",
            why_it_matters="今後の公式発表が注目されます。",
            category="国内",
            importance=5,
            tags=["地震"],
            facts=["関係機関が新しい情報を公表しました。"],
            impact=["沿岸部が発表の対象地域です。"],
            background="これまでの発表内容を踏まえた続報です。",
            watch_points=["次回の公式発表時刻"],
            source_notes={
                "nhk-top": "主要ニュースでは発表の概要を伝えています。",
                "nhk-world": "国際フィードでも同じ発表を扱っています。",
            },
            article_type="feature",
        )
        window = coverage_window(datetime(2026, 8, 15, 12, 10, tzinfo=JST), "12")
        edition = build_edition(
            window,
            [draft],
            candidates,
            datetime(2026, 8, 15, 12, 10, tzinfo=JST),
            "fallback",
            [],
            {"name": "テスト"},
        )

        article = edition["articles"][0]
        self.assertEqual(article["importance"], 3)
        self.assertEqual(article["facts"], ["関係機関が新しい情報を公表しました。"])
        self.assertEqual(article["impactPoints"], ["沿岸部が発表の対象地域です。"])
        self.assertEqual(article["background"], "これまでの発表内容を踏まえた続報です。")
        self.assertEqual(article["watchPoints"], ["次回の公式発表時刻"])
        self.assertEqual(article["articleType"], "feature")
        self.assertEqual(article["sourceCount"], 2)
        self.assertEqual(article["publisherCount"], 1)
        self.assertEqual(len(article["sources"]), 1)
        self.assertEqual(article["sources"][0]["publisherId"], "nhk")
        self.assertEqual(len(article["sources"][0]["keyPoints"]), 2)
        self.assertFalse(article["sources"][0]["isPrimary"])
        self.assertEqual(
            [section["heading"] for section in article["sections"]],
            ["確認できた事実", "影響・対象地域", "背景", "次に注目"],
        )
        self.assertEqual(len(article["updates"]), 2)
        self.assertEqual(edition["stats"]["sourceCount"], 2)
        self.assertEqual(edition["stats"]["publisherCount"], 1)
        self.assertEqual(edition["stats"]["publisherCounts"], {"nhk": 1})
        self.assertEqual(edition["stats"]["articleTypeCounts"], {"feature": 1})
        self.assertEqual(edition["generationMode"], "structured")
        self.assertIn("1配信元", edition["summary"])

    def test_publish_contract_caps_facts_and_deduplicates_sections(self):
        candidate = Candidate(
            id="official-1",
            title="公式情報を更新",
            description="公式情報の詳細です。",
            url="https://example.com/official/1",
            source_name="公的機関",
            category="国内",
            published_at=datetime(2026, 8, 15, 1, tzinfo=UTC),
            primary_source=True,
        )
        facts = [f"確認事実{index}。" for index in range(12)]
        target = "対象地域は沿岸部です。"
        draft = StoryDraft(
            candidate_ids=[candidate.id],
            title="公式情報を更新",
            dek="",
            summary="公式情報が更新されました。",
            why_it_matters="",
            category="国内",
            importance=4,
            tags=[],
            facts=facts,
            impact=[facts[0], target],
            background=target,
            watch_points=[facts[1], "次回発表は18時です。"],
            article_type="full",
        )
        window = coverage_window(datetime(2026, 8, 15, 12, 10, tzinfo=JST), "12")

        edition = build_edition(
            window,
            [draft],
            [candidate],
            datetime(2026, 8, 15, 12, 10, tzinfo=JST),
            "deterministic",
            [],
            {"name": "テスト"},
        )

        article = edition["articles"][0]
        self.assertEqual(len(article["facts"]), 10)
        self.assertEqual(article["impactPoints"], [target])
        self.assertEqual(article["background"], "")
        self.assertEqual(article["watchPoints"], ["次回発表は18時です。"])
        self.assertEqual(article["articleType"], "brief")
        self.assertEqual(edition["generationMode"], "structured")


if __name__ == "__main__":
    unittest.main()
