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
    _archive_context_candidates,
    _assert_publishable_feature_floor,
    _assert_publishable_source_mix,
    _edition_id_for_timestamp,
    _record_completion,
    _select_fresh_candidates,
    main,
)
from scripts.news_pipeline.editor import create_drafts
from scripts.news_pipeline.models import Candidate
from scripts.news_pipeline.publisher import build_edition
from scripts.news_pipeline.time_windows import JST, coverage_window


def make_candidate(
    identifier: str,
    title: str,
    *,
    source: str = "テスト通信",
    publisher: str = "test",
    category: str = "国内",
    description: str = "",
) -> Candidate:
    return Candidate(
        id=identifier,
        title=title,
        description=description,
        url=f"https://example.com/{identifier}",
        source_name=source,
        category=category,
        published_at=datetime(2026, 8, 15, 2, tzinfo=UTC),
        publisher_id=publisher,
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


def qualified_desk_feature(identifier: int):
    category, title = [
        ("国内", "子育て給付の所得要件撤廃、政府案と自治体への影響"),
        ("海外", "インドネシア東部地震、被害確認と救助活動が続く"),
        ("テクノロジー", "省電力AI半導体の試作結果、国内量産へ検証進む"),
    ][(identifier - 1) % 3]
    facts = [
        f"配信元A：対象となる制度・地域について確認事実{identifier}-{index}と"
        "具体的な実施内容が公表されました。"
        for index in range(3)
    ]
    sources = [
        {
            "publisherId": f"publisher-{source_index}",
            "name": f"配信元{source_index}",
            "url": f"https://example.com/{identifier}/{source_index}",
            "links": [
                {
                    "title": f"根拠記事{source_index}",
                    "url": f"https://example.com/{identifier}/{source_index}",
                }
            ],
            "isPrimary": False,
        }
        for source_index in range(2)
    ]
    return {
        "title": title,
        "dek": "08月15日 09:00〜08月15日 12:00更新。対象と実施時期が示されました。",
        "summary": (
            "関係機関は同じ出来事について新しい確認結果を示しました。"
            "対象となる地域や制度、実施時期が具体的に公表され、"
            "これまでの経過と次に予定される対応も明らかになっています。"
        ),
        "articleType": "feature",
        "facts": facts,
        "impactPoints": [
            "配信元B：対象地域の住民と関係事業者への具体的な影響が示されました。"
        ],
        "background": (
            "配信元A：これまでの経過では制度や支援体制の不足が課題とされ、"
            "関係機関が段階的に対応を進めてきました。"
        ),
        "watchPoints": [
            "配信元B：今後は追加の確認結果と実施日程が公表される予定です。"
        ],
        "tags": [category],
        "deskLens": "event",
        "eventKeys": [f"event-{identifier}"],
        "sourceCount": 2,
        "freshSourceCount": 2,
        "publisherCount": 2,
        "sources": sources,
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


class RollingContextTests(unittest.TestCase):
    def test_timestamp_maps_to_original_edition(self):
        self.assertEqual(
            _edition_id_for_timestamp(
                datetime(2026, 8, 15, 20, 30, tzinfo=JST)
            ),
            "2026-08-16-06",
        )
        self.assertEqual(
            _edition_id_for_timestamp(
                datetime(2026, 8, 15, 10, 30, tzinfo=JST)
            ),
            "2026-08-15-12",
        )

    def test_archive_context_keeps_origin_timestamp_and_source_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editions = root / "site" / "data" / "editions"
            editions.mkdir(parents=True)
            payload = {
                "edition": {"id": "2026-08-15-18"},
                "articles": [
                    {
                        "title": "自治体が新しい防災計画を公表",
                        "category": "国内",
                        "importance": 4,
                        "sourceCount": 1,
                        "facts": [
                            "自治体は避難所を二か所追加すると発表しました。"
                        ],
                        "impactPoints": [
                            "対象地域の住民に新しい避難経路を案内します。"
                        ],
                        "background": "従来の計画は五年前に策定されました。",
                        "watchPoints": ["説明会は翌月に開かれる予定です。"],
                        "sources": [
                            {
                                "name": "地域新聞",
                                "publisherId": "regional",
                                "url": "https://example.com/plan",
                                "publishedAt": "2026-08-15T16:20:00+09:00",
                                "isPrimary": False,
                                "keyPoints": ["防災計画の改定内容"],
                                "links": [
                                    {
                                        "title": "自治体が新しい防災計画を公表",
                                        "url": "https://example.com/plan",
                                        "publishedAt": "2026-08-15T16:20:00+09:00",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
            (editions / "2026-08-15-18.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

            context = _archive_context_candidates(
                root,
                datetime(2026, 8, 15, 6, 0, tzinfo=JST),
                datetime(2026, 8, 16, 6, 0, tzinfo=JST),
            )

        self.assertEqual(len(context), 1)
        candidate = context[0]
        self.assertTrue(candidate.context_only)
        self.assertEqual(candidate.origin_edition_id, "2026-08-15-18")
        self.assertEqual(candidate.url, "https://example.com/plan")
        self.assertEqual(
            candidate.published_at.isoformat(), "2026-08-15T16:20:00+09:00"
        )
        self.assertIn("避難所を二か所", candidate.description)


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


class FeatureFloorTests(unittest.TestCase):
    def test_unrelated_real_feed_shape_publishes_without_fake_features(self):
        records = [
            ("mainichi-indonesia", "インドネシア政府が首都移転の工程を公表", "毎日新聞", "mainichi", "国内"),
            ("nhk-ukraine", "ウクライナ支援をめぐり各国首脳が協議", "NHK ONE 国際", "nhk", "国内"),
            ("asahi-childcare", "政府が子育て給付の対象拡大を決定", "朝日新聞", "asahi", "国内"),
            ("kyodo-shelter", "台風接近で自治体が避難所を追加開設", "共同通信", "kyodo", "社会"),
            ("mainichi-koshien", "甲子園で準々決勝の組み合わせ決まる", "毎日新聞", "mainichi", "国内"),
            ("asahi-pitcher", "先発投手が完封し次戦へ向け調整", "朝日新聞", "asahi", "国内"),
            ("nhk-baseball", "プロ野球の首位争いは一ゲーム差に", "NHK ONE スポーツ", "nhk", "スポーツ"),
            ("kyodo-film", "国際映画祭がコンペ部門の出品作を発表", "共同通信", "kyodo", "エンタメ"),
            ("itmedia-claude", "Claude生成文を識別する透かし技術を検証", "ITmedia NEWS", "itmedia", "テクノロジー"),
            ("itmedia-game", "ゲーム配信基盤が大型更新の内容を公開", "ITmedia NEWS", "itmedia", "テクノロジー"),
            ("asahi-chip", "国内企業が省電力AI半導体を発表", "朝日新聞", "asahi", "テクノロジー"),
            ("mainichi-rail", "鉄道各社がお盆期間の利用実績を公表", "毎日新聞", "mainichi", "経済"),
            ("nhk-science", "大学が地震被害予測の共同研究を開始", "NHK ONE 科学・文化", "nhk", "科学"),
            ("kyodo-un", "国連総会が人道支援の追加決議を採択", "共同通信", "kyodo", "海外"),
        ]
        candidates = [
            make_candidate(
                identifier,
                title,
                source=source,
                publisher=publisher,
                category=category,
                description=(
                    f"{source}は「{title}」について公表された具体的な内容を伝えました。"
                    "発表では対象となる地域や団体と実施時期が示されています。"
                    "関係機関は今後の日程も公表する予定です。"
                ),
            )
            for identifier, title, source, publisher, category in records
        ]
        drafts, mode = create_drafts(candidates)
        window = coverage_window(
            datetime(2026, 8, 15, 12, 10, tzinfo=JST), "12"
        )
        edition = build_edition(
            window,
            drafts,
            candidates,
            datetime(2026, 8, 15, 12, 10, tzinfo=JST),
            mode,
            [],
            {"name": "テスト"},
        )

        _assert_publishable_feature_floor(edition)
        self.assertEqual(edition["stats"]["articleTypeCounts"].get("feature", 0), 0)
        self.assertTrue(edition["articles"])

    def test_allows_brief_only_edition_for_archive_feature_backfill(self):
        edition = {
            "edition": {"id": "2026-08-15-12"},
            "articles": [
                {
                    "articleType": "brief",
                    "sources": [
                        {
                            "publisherId": f"publisher-{index}",
                            "url": f"https://example.com/brief/{index}",
                        }
                    ],
                }
                for index in range(4)
            ],
        }

        _assert_publishable_feature_floor(edition)

    def test_allows_any_count_of_qualified_single_event_features(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                edition = {
                    "edition": {"id": "2026-08-15-12"},
                    "articles": [
                        qualified_desk_feature(index)
                        for index in range(1, count + 1)
                    ],
                }
                _assert_publishable_feature_floor(edition)

    def test_allows_natural_japanese_time_scope_for_structured_jma_feature(self):
        article = qualified_desk_feature(1)
        article.update(
            {
                "dek": (
                    "8月16日12時04分、熊本県熊本地方を震源とする"
                    "マグニチュード3.6の地震があり、最大震度3を観測しました。"
                    "津波の心配はありません。"
                ),
                "facts": [
                    "発生時刻は8月16日12時04分です。",
                    "震源は熊本県熊本地方です。",
                    "地震の規模はマグニチュード3.6です。",
                    "最大震度は3です。",
                ],
                "impactPoints": [
                    "この地震による津波の心配はありません。",
                    "熊本県熊本と益城町で震度3を観測しました。",
                ],
                "background": "",
                "watchPoints": [],
                "sourceCount": 1,
                "freshSourceCount": 1,
                "publisherCount": 1,
                "sources": [
                    {
                        "publisherId": "jma",
                        "name": "気象庁",
                        "links": [
                            {
                                "title": "熊本県熊本地方で地震",
                                "url": "https://www.data.jma.go.jp/example.xml",
                            }
                        ],
                        "isPrimary": True,
                    }
                ],
            }
        )
        article["summary"] = "".join(article["facts"] + article["impactPoints"])

        _assert_publishable_feature_floor(
            {"edition": {"id": "2026-08-16-18"}, "articles": [article]}
        )

    def test_rejects_duplicate_feature_title(self):
        articles = [qualified_desk_feature(index) for index in (1, 2, 3)]
        articles[2]["title"] = articles[0]["title"]
        edition = {"edition": {"id": "2026-08-15-12"}, "articles": articles}

        with self.assertRaisesRegex(RuntimeError, "repeats a feature title"):
            _assert_publishable_feature_floor(edition)

    def test_rejects_event_reused_across_features(self):
        articles = [qualified_desk_feature(index) for index in (1, 2, 3)]
        articles[2]["eventKeys"][0] = articles[0]["eventKeys"][0]

        with self.assertRaisesRegex(RuntimeError, "reuses an event"):
            _assert_publishable_feature_floor(
                {"edition": {"id": "2026-08-15-12"}, "articles": articles}
            )

    def test_rejects_feature_with_multiple_event_keys(self):
        article = qualified_desk_feature(1)
        article["eventKeys"].append("unrelated-event")

        with self.assertRaisesRegex(RuntimeError, "unqualified or mixed-event"):
            _assert_publishable_feature_floor(
                {"edition": {"id": "2026-08-15-12"}, "articles": [article]}
            )

    def test_rejects_incomplete_or_overlong_feature_title(self):
        article = qualified_desk_feature(1)
        article["title"] = "政府が「子育て給付の対象拡大を決定"
        with self.assertRaisesRegex(RuntimeError, "unqualified or mixed-event"):
            _assert_publishable_feature_floor(
                {"edition": {"id": "2026-08-15-12"}, "articles": [article]}
            )

        article = qualified_desk_feature(1)
        article["title"] = "政府の子育て給付拡大" + "詳細" * 60
        with self.assertRaisesRegex(RuntimeError, "unqualified or mixed-event"):
            _assert_publishable_feature_floor(
                {"edition": {"id": "2026-08-15-12"}, "articles": [article]}
            )

    def test_rejects_feature_whose_length_is_only_editorial_framing(self):
        article = qualified_desk_feature(1)
        article["facts"] = ["短い事実です。"]
        article["impactPoints"] = []
        article["background"] = ""
        article["watchPoints"] = []
        article["summary"] = "編集手順の説明" * 100
        article["whyItMatters"] = "比較できる" * 100
        edition = {
            "edition": {"id": "2026-08-15-12"},
            "articles": [article],
        }

        with self.assertRaisesRegex(RuntimeError, "unqualified or mixed-event"):
            _assert_publishable_feature_floor(edition)

    def test_rejects_feature_made_only_from_old_context(self):
        article = qualified_desk_feature(1)
        article["freshSourceCount"] = 0
        edition = {
            "edition": {"id": "2026-08-16-06"},
            "articles": [article],
        }

        with self.assertRaisesRegex(RuntimeError, "unqualified or mixed-event"):
            _assert_publishable_feature_floor(edition)

    def test_rejects_procedural_or_truncated_feature_copy(self):
        article = qualified_desk_feature(1)
        article["facts"].append("配信元Aは新しい情報を発表したと配信。")
        with self.assertRaisesRegex(RuntimeError, "unqualified or mixed-event"):
            _assert_publishable_feature_floor(
                {"edition": {"id": "2026-08-15-12"}, "articles": [article]}
            )

        article = qualified_desk_feature(1)
        article["facts"].append("発達した積乱雲の近づく兆しがあ。")
        with self.assertRaisesRegex(RuntimeError, "unqualified or mixed-event"):
            _assert_publishable_feature_floor(
                {"edition": {"id": "2026-08-15-12"}, "articles": [article]}
            )

    def test_brief_only_main_run_publishes_and_advances_state(self):
        candidate = make_candidate("brief-only", "政府が新制度を発表")
        brief_only = {
            "edition": {"id": "2026-08-15-12"},
            "articles": [
                {
                    "title": f"短報{index}",
                    "publishedAt": "2026-08-15T11:00:00+09:00",
                    "articleType": "brief",
                    "sources": [
                        {
                            "publisherId": f"publisher-{index}",
                            "name": f"配信元{index}",
                            "url": f"https://example.com/brief/{index}",
                            "isPrimary": False,
                        }
                    ],
                }
                for index in range(4)
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config" / "site.json").write_text("{}", encoding="utf-8")
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
                "scripts.collect_news.build_edition", return_value=brief_only
            ), patch("scripts.collect_news.publish_edition") as publish:
                main()

            publish.assert_called_once()
            self.assertTrue((root / ".state" / "news-state.json").exists())


if __name__ == "__main__":
    unittest.main()
