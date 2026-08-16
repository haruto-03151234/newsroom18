import json
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.news_pipeline.feeds import (
    DEFAULT_MAX_FEED_BYTES,
    MAX_FEED_BYTES,
    _enrich_linked_candidate,
    _entry_categories_are_allowed,
    _entry_to_candidate,
    _extract_official_html_detail,
    _extract_jma_detail,
    _feed_byte_limit,
    _fetch_linked_html,
    _fetch_linked_xml,
    _fetch_one,
    _validate_patterns,
    collect_candidates,
    load_feed_config,
)


ROOT = Path(__file__).resolve().parents[1]

EARTHQUAKE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/"
        xmlns:jmx_eb="http://xml.kishou.go.jp/jmaxml1/elementBasis1/">
  <Control><Title>震源・震度に関する情報</Title></Control>
  <Head xmlns="http://xml.kishou.go.jp/jmaxml1/informationBasis1/">
    <Title>震源・震度情報</Title>
    <EventID>20260815101234</EventID>
    <Serial>2</Serial>
    <Headline>
      <Text>この地震による津波の心配はありません。</Text>
      <Information><Item><Kind><Name>震度4</Name></Kind><Areas><Area><Name>石川県</Name></Area></Areas></Item></Information>
    </Headline>
  </Head>
  <Body xmlns="http://xml.kishou.go.jp/jmaxml1/body/seismology1/">
    <Earthquake>
      <OriginTime>2026-08-15T10:12:34+09:00</OriginTime>
      <Hypocenter><Area><Name>石川県能登地方</Name></Area></Hypocenter>
      <jmx_eb:Magnitude description="Ｍ４．２">4.2</jmx_eb:Magnitude>
    </Earthquake>
    <Intensity><Observation><MaxInt>4</MaxInt></Observation></Intensity>
    <Comments><ForecastComment><Text>この地震による津波の心配はありません。</Text></ForecastComment></Comments>
  </Body>
</Report>""".encode()

WEAK_EARTHQUAKE_XML = EARTHQUAKE_XML.replace(b"<MaxInt>4</MaxInt>", b"<MaxInt>2</MaxInt>")

INTENSITY_BULLETIN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Control><Title>震度速報</Title></Control>
  <Head>
    <Title>震度速報</Title><EventID>20260815101234</EventID><Serial>1</Serial>
    <Headline><Text>石川県で震度4を観測しました。</Text></Headline>
  </Head>
  <Body><Intensity><Observation><MaxInt>4</MaxInt></Observation></Intensity></Body>
</Report>""".encode()

WEATHER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Control><Title>府県気象情報</Title></Control>
  <Head>
    <Title>台風第7号に関する東京都気象情報</Title>
    <Headline>
      <Text>台風第7号は15日夜に東京地方へ接近する見込みです。</Text>
      <Information><Item><Kind><Name>暴風警報</Name></Kind><Areas><Area><Name>東京地方</Name></Area></Areas></Item></Information>
    </Headline>
  </Head>
  <Body><MeteorologicalInfos><MeteorologicalInfo><Text>最大瞬間風速は35メートルと予想されています。</Text></MeteorologicalInfo></MeteorologicalInfos></Body>
</Report>""".encode()

VOLCANO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Control><Title>火山の状況に関する解説情報</Title></Control>
  <Head>
    <Title>火山の状況に関する解説情報</Title>
    <Headline>
      <Text>桜島では噴火警戒レベル3が継続しています。</Text>
      <Information><Item><Kind><Name>噴火警報</Name></Kind><Areas><Area><Name>桜島</Name></Area></Areas></Item></Information>
    </Headline>
  </Head>
  <Body><VolcanoInfo><Text>火山性地震を24時間で10回観測しました。</Text></VolcanoInfo></Body>
</Report>""".encode()

ROUTINE_WARNING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Control><Title>気象特別警報・警報・注意報</Title></Control>
  <Head><Title>東京都気象警報・注意報</Title><Headline><Text>東京都では強風に注意してください。</Text></Headline></Head>
</Report>""".encode()

SPECIFIC_WARNING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Control><Title>土砂災害警戒情報</Title></Control>
  <Head><Title>青森県レベル4土砂災害危険警報</Title><Headline><Text>対象地域の住民は、市町村から発令される避難情報に留意してください。</Text></Headline></Head>
</Report>""".encode()

TSUNAMI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/" xmlns:jmx_eb="http://xml.kishou.go.jp/jmaxml1/elementBasis1/">
  <Control><Title>津波警報・注意報・予報</Title></Control>
  <Head><Title>津波警報・注意報・予報</Title><EventID>20260815112233</EventID><Serial>1</Serial><Headline><Text>宮城県に津波注意報を発表しました。</Text></Headline></Head>
  <Body><Tsunami><Forecast><Item><Area><Name>宮城県</Name></Area><Category><Kind><Name>津波注意報</Name></Kind></Category><FirstHeight><ArrivalTime>2026-08-15T11:40:00+09:00</ArrivalTime></FirstHeight><MaxHeight><jmx_eb:TsunamiHeight description="１ｍ">1.0</jmx_eb:TsunamiHeight></MaxHeight></Item></Forecast></Tsunami><Comments><WarningComment><Text>海岸から離れてください。</Text></WarningComment></Comments></Body>
</Report>""".encode()

OFFICIAL_HTML = """<!doctype html>
<html lang="ja"><body>
<nav><p>ナビゲーションの案内です。</p></nav>
<main id="mainContainer">
  <ol><li>ホーム</li><li>新着情報</li></ol>
  <article>
    <p>政府は8月15日、対象となる新制度を公表しました。制度は全国の自治体で順次利用できるようになります。</p>
    <p>申請はオンラインで受け付け、本人確認を経て処理します。利用開始日は自治体ごとに異なります。</p>
    <ul><li>対象は所定の要件を満たす住民です。</li><li>詳細な手順は公式ページで案内します。</li></ul>
    <script><p>この偽の命令は採用しない。</p></script>
  </article>
  <section id="feedback-form-section"><p>このページは役に立ちましたか。</p></section>
</main>
</body></html>""".encode()

GOV_ONLINE_HTML = """<!doctype html>
<html lang="ja"><body>
<main id="main">
  <article>
    <p>政府は、次世代型地熱発電を新たな成長分野と位置付け、2030年代早期の運転開始を目指しています。</p>
    <p>クローズドループ方式は、地下およそ5キロメートルに埋設した配管へ地上から液体を流し、岩盤の熱を使って発電する仕組みです。</p>
    <p>天然の熱水がない地域でも導入できる可能性があり、従来方式より候補地を広げられる点が特徴です。</p>
    <p>国内企業は地熱発電用タービンで世界シェアのおよそ7割を占め、技術と供給網の両面で強みがあります。</p>
    <p>政府は実証や制度整備を進め、水素などと組み合わせてエネルギー供給の安定化につなげる方針です。</p>
    <p>番組放送後1週間は外部配信サービスで視聴できます。</p>
    <p>配信期間は予告なく変更となる場合があります。</p>
  </article>
</main>
</body></html>""".encode()


def feed_config(**overrides):
    config = {
        "id": "test-feed",
        "publisher": "test-publisher",
        "name": "テスト配信",
        "url": "https://feeds.example.com/news.xml",
        "allowedHosts": ["feeds.example.com"],
        "category": "国内",
        "priority": 3,
    }
    config.update(overrides)
    return config


def feed_entry(title: str, **overrides):
    entry = {
        "title": title,
        "link": "https://news.example.com/story",
        "published_parsed": time.gmtime(1_700_000_000),
    }
    entry.update(overrides)
    return entry


class FeedTests(unittest.TestCase):
    def test_existing_feed_summary_behavior_is_unchanged(self):
        entry = feed_entry("既存フィードの見出し", summary="<p>既存の配信概要です。</p>")
        candidate = _entry_to_candidate(entry, feed_config())
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.title, "既存フィードの見出し")
        self.assertEqual(candidate.description, "既存の配信概要です。")

    def test_mixed_feed_category_inference_uses_only_supplied_metadata(self):
        config = feed_config(inferCategory=True)
        cases = (
            ("有明の投手が試合前練習で負傷 夏の甲子園", "スポーツ"),
            ("インドネシア政府が無料給食制度を見直し", "海外"),
            ("ロシア経済への制裁で銀行取引を制限", "海外"),
            ("ウクライナで患者を受け入れる病院を支援", "海外"),
            ("プロテイン高騰 2年で価格ほぼ倍", "経済"),
            ("ホラーゲームの物語をマンガ家が解説", "エンタメ"),
            ("Claudeの生成AIに見えない透かし", "テクノロジー"),
            ("花火大会の事故で3人軽傷", "社会"),
            ("NEURALモデルの新しい評価手法", "国内"),
        )
        for title, expected in cases:
            with self.subTest(title=title):
                candidate = _entry_to_candidate(feed_entry(title), config)
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate.category, expected)

    def test_feed_category_tags_take_precedence_over_title_keywords(self):
        cases = (
            ("英国籍の少女を横浜で保護", "社会", "社会"),
            ("横浜が無安打で守り切る", "スポーツ", "スポーツ"),
            ("企業の新サービス", "ビジネス", "経済"),
            ("海外市場について首相が説明", "政治", "国内"),
            ("医療支援を発表", "国際", "海外"),
            ("AIを使った研究", "サイエンス", "科学"),
            ("作家の新刊", "文化芸能", "エンタメ"),
        )
        config = feed_config(inferCategory=True)
        for title, tag, expected in cases:
            with self.subTest(tag=tag):
                candidate = _entry_to_candidate(
                    feed_entry(title, tags=[{"term": tag}]), config
                )
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate.category, expected)

    def test_comma_separated_official_subjects_are_inferred_individually(self):
        config = feed_config(inferCategory=True)
        cases = (
            ("次世代型の仕組みを紹介", "テレビ番組,エネルギー", "経済"),
            ("国の仕事を紹介", "ラジオ番組,労働", "経済"),
            ("被害防止策を紹介", "防犯,安心・安全（その他）", "社会"),
        )
        for title, subject, expected in cases:
            with self.subTest(subject=subject):
                candidate = _entry_to_candidate(
                    feed_entry(title, tags=[{"term": subject}]), config
                )
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate.category, expected)

    def test_unknown_feed_category_tag_falls_back_to_title_then_default(self):
        config = feed_config(inferCategory=True)
        sports = _entry_to_candidate(
            feed_entry("夏の甲子園が開幕", tags=[{"term": "ニュース"}]), config
        )
        default = _entry_to_candidate(
            feed_entry("新しい取り組みを発表", tags=[{"label": "ニュース"}]),
            config,
        )
        self.assertEqual(sports.category, "スポーツ")
        self.assertEqual(default.category, "国内")

    def test_category_inference_is_opt_in_for_specialist_feeds(self):
        candidate = _entry_to_candidate(
            feed_entry("海外市場で株価が上昇"),
            feed_config(category="海外"),
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.category, "海外")

    def test_atom_content_can_supply_description_and_bracket_title(self):
        config = feed_config(
            extractAtomContent=True,
            titleFromDescriptionBrackets=True,
            includeTitlePatterns=["土砂災害警戒", "台風"],
        )
        entry = feed_entry(
            "府県気象情報",
            content=[
                {
                    "type": "text/plain",
                    "value": "【土砂災害警戒情報】県内では土砂災害に警戒してください。",
                }
            ],
        )
        candidate = _entry_to_candidate(entry, config)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.title, "土砂災害警戒情報")
        self.assertEqual(
            candidate.description,
            "【土砂災害警戒情報】県内では土砂災害に警戒してください。",
        )

    def test_include_and_exclude_title_patterns_filter_entries(self):
        config = feed_config(
            extractAtomContent=True,
            titleFromDescriptionBrackets=True,
            includeTitlePatterns=["震源・震度", "降灰"],
            excludeTitlePatterns=["降灰予報.*定時", "火山観測報"],
        )
        accepted = feed_entry(
            "地震情報",
            content=[{"value": "【震源・震度情報】最大震度4を観測しました。"}],
        )
        excluded = feed_entry(
            "火山情報",
            content=[{"value": "【降灰予報（定時）】定時の降灰予報です。"}],
        )
        unmatched = feed_entry(
            "火山情報",
            content=[{"value": "【通常の火山情報】観測結果を掲載します。"}],
        )
        self.assertIsNotNone(_entry_to_candidate(accepted, config))
        self.assertIsNone(_entry_to_candidate(excluded, config))
        self.assertIsNone(_entry_to_candidate(unmatched, config))

    def test_product_title_filter_is_separate_from_derived_title_filter(self):
        config = feed_config(
            extractAtomContent=True,
            titleFromDescriptionBrackets=True,
            includeProductTitlePatterns=["^震源・震度に関する情報$"],
        )
        entry = feed_entry(
            "震源・震度に関する情報",
            content=[{"value": "【石川県能登地方】地震情報を発表しました。"}],
        )
        candidate = _entry_to_candidate(entry, config)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.title, "石川県能登地方")

    def test_generic_warning_product_does_not_satisfy_detail_severity_filter(self):
        config = feed_config(
            extractAtomContent=True,
            titleFromDescriptionBrackets=True,
            includeProductTitlePatterns=["^気象特別警報・警報・注意報$"],
            includeTitlePatterns=["特別警報"],
            fetchLinkedXml=True,
            linkedXmlParser="jma",
            linkedXmlIncludePatterns=["特別警報"],
        )
        routine_entry = feed_entry(
            "気象特別警報・警報・注意報",
            content=[{"value": "【東京都気象警報・注意報】強風注意報を発表しました。"}],
        )
        self.assertIsNone(_entry_to_candidate(routine_entry, config))

        severe_atom_entry = feed_entry(
            "気象特別警報・警報・注意報",
            content=[{"value": "【東京都大雨特別警報】大雨特別警報を発表しました。"}],
        )
        candidate = _entry_to_candidate(severe_atom_entry, config)
        self.assertIsNotNone(candidate)
        with patch(
            "scripts.news_pipeline.feeds._fetch_linked_xml",
            return_value=ROUTINE_WARNING_XML,
        ):
            self.assertIsNone(
                _enrich_linked_candidate(candidate, severe_atom_entry, config)
            )

    def test_live_shaped_soil_and_gust_titles_pass_derived_prefilter(self):
        feeds = load_feed_config(ROOT / "config" / "feeds.json")
        extra = next(item for item in feeds if item["id"] == "jma-extra")
        soil = feed_entry(
            "土砂災害警戒情報",
            content=[
                {
                    "value": (
                        "【青森県レベル４土砂災害危険警報】"
                        "土砂災害の危険度が高まっています。"
                    )
                }
            ],
        )
        gust = feed_entry(
            "府県気象情報",
            content=[
                {
                    "value": (
                        "【気象解説情報（大雨・落雷・突風）】"
                        "落雷や激しい突風に注意してください。"
                    )
                }
            ],
        )
        soil_candidate = _entry_to_candidate(soil, extra)
        self.assertIsNotNone(soil_candidate)
        self.assertIsNotNone(_entry_to_candidate(gust, extra))
        with patch(
            "scripts.news_pipeline.feeds._fetch_linked_xml",
            return_value=SPECIFIC_WARNING_XML,
        ):
            enriched = _enrich_linked_candidate(soil_candidate, soil, extra)
        self.assertIsNotNone(enriched)
        self.assertEqual(enriched.title, "青森県レベル4土砂災害危険警報")

    def test_per_feed_size_limit_defaults_and_caps_at_eight_megabytes(self):
        self.assertEqual(_feed_byte_limit(feed_config()), DEFAULT_MAX_FEED_BYTES)
        self.assertEqual(
            _feed_byte_limit(feed_config(maxFeedBytes=MAX_FEED_BYTES)),
            MAX_FEED_BYTES,
        )
        with self.assertRaises(ValueError):
            _feed_byte_limit(feed_config(maxFeedBytes=MAX_FEED_BYTES + 1))
        with self.assertRaises(ValueError):
            _feed_byte_limit(feed_config(maxFeedBytes=True))

    def test_fetch_uses_configured_size_limit_without_network(self):
        response = MagicMock()
        response.headers = {}
        response.read.return_value = b"<rss><channel></channel></rss>"
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        config = feed_config(maxFeedBytes=3_000_000)
        with patch(
            "scripts.news_pipeline.feeds.urllib.request.build_opener",
            return_value=opener,
        ):
            self.assertEqual(_fetch_one(config), [])
        response.read.assert_called_once_with(3_000_001)

    def test_pattern_validation_rejects_invalid_regex(self):
        with self.assertRaises(ValueError):
            _validate_patterns(["["], "includeTitlePatterns")

    def test_entry_category_filter_keeps_only_configured_official_release_type(self):
        config = feed_config(includeEntryCategories=["報道発表"])
        release = feed_entry("制度を公表", tags=[{"term": "報道発表"}])
        procurement = feed_entry("調達情報", tags=[{"term": "調達情報"}])
        self.assertTrue(_entry_categories_are_allowed(release, config))
        self.assertFalse(_entry_categories_are_allowed(procurement, config))
        self.assertIsNotNone(_entry_to_candidate(release, config))
        self.assertIsNone(_entry_to_candidate(procurement, config))

    def test_official_html_parser_prefers_article_and_ignores_page_chrome(self):
        config = feed_config(linkedHtmlRootIds=[])
        detail = _extract_official_html_detail(
            OFFICIAL_HTML, config, "制度を公表"
        )
        self.assertIn("政府は8月15日", detail)
        self.assertIn("対象は所定の要件", detail)
        self.assertNotIn("ナビゲーション", detail)
        self.assertNotIn("ホーム", detail)
        self.assertNotIn("役に立ちましたか", detail)
        self.assertNotIn("偽の命令", detail)

    def test_official_html_parser_can_require_a_legacy_content_root(self):
        payload = """
        <html><body><div><p>本文ではない案内です。</p></div>
        <div id="main_content">
          <p>農林水産省は調査結果を公表しました。対象地域は全国です。</p>
          <p>前年度と比べて指標は3ポイント改善し、次回は9月に更新します。</p>
          <p>追加の内訳は都道府県別の資料に掲載しています。</p>
        </div></body></html>
        """.encode()
        detail = _extract_official_html_detail(
            payload, feed_config(linkedHtmlRootIds=["main_content"]), "調査結果"
        )
        self.assertIn("3ポイント改善", detail)
        self.assertNotIn("本文ではない", detail)

    def test_government_online_parser_keeps_facts_and_drops_program_metadata(self):
        config = feed_config(linkedHtmlRootIds=["main"])
        detail = _extract_official_html_detail(
            GOV_ONLINE_HTML, config, "エネルギーの新技術"
        )
        self.assertGreaterEqual(len(detail), 240)
        self.assertIn("2030年代早期", detail)
        self.assertIn("世界シェアのおよそ7割", detail)
        self.assertNotIn("番組放送後", detail)
        self.assertNotIn("配信期間", detail)

    def test_linked_html_fetch_requires_allowlisted_https_path_type_and_limits(self):
        config = feed_config(
            allowedHosts=["www.digital.go.jp"],
            linkedHtmlPathPrefixes=["/news/"],
            linkedHtmlMaxBytes=1000,
            linkedHtmlTimeoutSeconds=7,
        )
        with self.assertRaises(RuntimeError):
            _fetch_linked_html("https://attacker.example/news/story", config)
        with self.assertRaises(RuntimeError):
            _fetch_linked_html("http://www.digital.go.jp/news/story", config)
        with self.assertRaises(RuntimeError):
            _fetch_linked_html("https://www.digital.go.jp/procurement/story", config)
        with self.assertRaises(RuntimeError):
            _fetch_linked_html("https://www.digital.go.jp/news/report.pdf", config)

        response = MagicMock()
        response.headers = {"Content-Type": "text/html; charset=UTF-8"}
        response.geturl.return_value = "https://www.digital.go.jp/news/story"
        response.read.return_value = OFFICIAL_HTML
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        with patch(
            "scripts.news_pipeline.feeds.urllib.request.build_opener",
            return_value=opener,
        ) as build_opener:
            payload = _fetch_linked_html(
                "https://www.digital.go.jp/news/story", config
            )
        self.assertEqual(payload, OFFICIAL_HTML)
        response.read.assert_called_once_with(1001)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 7)
        self.assertEqual(
            build_opener.call_args.args[0].allowed_path_prefixes, ("/news/",)
        )

        response.headers = {"Content-Type": "application/pdf"}
        with patch(
            "scripts.news_pipeline.feeds.urllib.request.build_opener",
            return_value=opener,
        ), self.assertRaises(RuntimeError):
            _fetch_linked_html("https://www.digital.go.jp/news/story", config)

        response.headers = {
            "Content-Type": "text/html",
            "Content-Length": "1001",
        }
        with patch(
            "scripts.news_pipeline.feeds.urllib.request.build_opener",
            return_value=opener,
        ), self.assertRaises(RuntimeError):
            _fetch_linked_html("https://www.digital.go.jp/news/story", config)

    def test_linked_html_can_require_an_exact_host(self):
        config = feed_config(
            url="https://www.gov-online.go.jp/rss/index.rdf",
            allowedHosts=["www.gov-online.go.jp"],
            linkedHtmlPathPrefixes=["/article/"],
            linkedHtmlRequireExactHost=True,
        )
        with self.assertRaises(RuntimeError):
            _fetch_linked_html(
                "https://sub.www.gov-online.go.jp/article/story.html", config
            )

    def test_linked_html_config_rejects_unsafe_or_ambiguous_settings(self):
        base = feed_config(
            fetchLinkedHtml=True,
            linkedHtmlParser="official",
            linkedHtmlPathPrefixes=["/news/"],
        )
        for override in (
            {"linkedHtmlParser": "generic"},
            {"linkedHtmlPathPrefixes": ["news/"]},
            {"fetchLinkedXml": True, "linkedXmlParser": "jma"},
            {"linkedHtmlRequireExactHost": "true"},
        ):
            item = dict(base)
            item.update(override)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "feeds.json"
                path.write_text(json.dumps({"feeds": [item]}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_feed_config(path)

    def test_infer_category_config_must_be_boolean(self):
        item = feed_config(inferCategory="true")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feeds.json"
            path.write_text(json.dumps({"feeds": [item]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inferCategory must be a boolean"):
                load_feed_config(path)

    def test_linked_xml_fetch_requires_allowlisted_https_and_enforces_limits(self):
        config = feed_config(
            allowedHosts=["data.jma.go.jp"],
            linkedXmlMaxBytes=1000,
            linkedXmlTimeoutSeconds=7,
        )
        with self.assertRaises(RuntimeError):
            _fetch_linked_xml("https://attacker.example/report.xml", config)
        with self.assertRaises(RuntimeError):
            _fetch_linked_xml("http://www.data.jma.go.jp/report.xml", config)

        response = MagicMock()
        response.headers = {}
        response.read.return_value = b"<?xml version='1.0'?><Report/>"
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        with patch(
            "scripts.news_pipeline.feeds.urllib.request.build_opener",
            return_value=opener,
        ):
            payload = _fetch_linked_xml(
                "https://www.data.jma.go.jp/developer/xml/data/report.xml",
                config,
            )
        self.assertEqual(payload, response.read.return_value)
        response.read.assert_called_once_with(1001)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 7)

        response.read.return_value = b"<?xml version='1.0'?><!DOCTYPE Report><Report/>"
        with patch(
            "scripts.news_pipeline.feeds.urllib.request.build_opener",
            return_value=opener,
        ), self.assertRaises(RuntimeError):
            _fetch_linked_xml(
                "https://www.data.jma.go.jp/developer/xml/data/report.xml",
                config,
            )

    def test_jma_earthquake_fields_create_specific_factual_story(self):
        detail = _extract_jma_detail(
            EARTHQUAKE_XML, "震源・震度に関する情報"
        )
        self.assertEqual(
            detail.title, "石川県能登地方で地震 M4.2 最大震度4"
        )
        self.assertEqual(detail.max_intensity, "4")
        self.assertEqual(detail.event_id, "20260815101234")
        self.assertEqual(detail.serial, "2")
        self.assertIn("2026-08-15T10:12:34+09:00", detail.description)
        self.assertIn("石川県能登地方", detail.description)
        self.assertIn("Ｍ４．２", detail.description)
        self.assertIn("最大震度: 4", detail.description)
        self.assertIn("津波の心配はありません", detail.description)
        self.assertLess(
            detail.description.index("津波の心配はありません"),
            detail.description.index("震度4: 石川県"),
        )

    def test_jma_weather_and_volcano_text_preserve_areas_numbers_and_wording(self):
        weather = _extract_jma_detail(WEATHER_XML, "府県気象情報")
        self.assertIn("台風第7号", weather.title)
        self.assertIn("暴風警報: 東京地方", weather.description)
        self.assertIn("最大瞬間風速は35メートル", weather.description)

        volcano = _extract_jma_detail(
            VOLCANO_XML, "火山の状況に関する解説情報"
        )
        self.assertEqual(volcano.title, "火山の状況に関する解説情報")
        self.assertIn("噴火警戒レベル3", volcano.description)
        self.assertIn("噴火警報: 桜島", volcano.description)
        self.assertIn("24時間で10回", volcano.description)

        warning = _extract_jma_detail(SPECIFIC_WARNING_XML, "土砂災害警戒情報")
        self.assertEqual(warning.title, "青森県レベル4土砂災害危険警報")
        self.assertIn("避難情報に留意", warning.description)

        tsunami = _extract_jma_detail(TSUNAMI_XML, "津波警報・注意報・予報")
        self.assertIn("宮城県に津波注意報", tsunami.description)
        self.assertIn("対象地域 宮城県", tsunami.description)
        self.assertIn("予想される最大波 １ｍ", tsunami.description)
        self.assertIn("2026-08-15T11:40:00+09:00", tsunami.description)
        self.assertIn("海岸から離れてください", tsunami.description)

    def test_weak_earthquake_is_filtered_but_emergency_product_is_kept(self):
        config = feed_config(
            fetchLinkedXml=True,
            linkedXmlParser="jma",
            minimumMaxIntensity=3,
            minimumMaxIntensityExemptPatterns=["津波", "噴火", "火山", "緊急"],
        )
        routine_entry = feed_entry("震源・震度に関する情報")
        routine = _entry_to_candidate(routine_entry, config)
        self.assertIsNotNone(routine)
        with patch(
            "scripts.news_pipeline.feeds._fetch_linked_xml",
            return_value=WEAK_EARTHQUAKE_XML,
        ):
            self.assertIsNone(
                _enrich_linked_candidate(routine, routine_entry, config)
            )

        emergency_entry = feed_entry("緊急地震速報（警報）")
        emergency = _entry_to_candidate(emergency_entry, config)
        self.assertIsNotNone(emergency)
        with patch(
            "scripts.news_pipeline.feeds._fetch_linked_xml",
            return_value=WEAK_EARTHQUAKE_XML,
        ):
            kept = _enrich_linked_candidate(emergency, emergency_entry, config)
        self.assertIsNotNone(kept)
        self.assertIn("最大震度2", kept.title)

    def test_linked_xml_is_fetched_only_for_candidates_in_coverage(self):
        inside = feed_entry(
            "震源・震度に関する情報",
            published_parsed=time.gmtime(1_700_000_000),
        )
        outside = feed_entry(
            "震源・震度に関する情報",
            link="https://news.example.com/outside",
            published_parsed=time.gmtime(1_699_900_000),
        )
        config = feed_config(
            fetchLinkedXml=True,
            linkedXmlParser="jma",
            linkedXmlRequired=True,
        )
        start = datetime.fromtimestamp(1_699_999_000, tz=UTC)
        end = datetime.fromtimestamp(1_700_001_000, tz=UTC)
        with patch(
            "scripts.news_pipeline.feeds._fetch_one", return_value=[inside, outside]
        ), patch(
            "scripts.news_pipeline.feeds._fetch_linked_xml",
            return_value=EARTHQUAKE_XML,
        ) as fetch_detail:
            candidates, failures = collect_candidates(
                [config], start, end, grace_hours=0, max_workers=1
            )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(failures, [])
        fetch_detail.assert_called_once()

    def test_official_linked_html_enriches_only_in_window_candidates(self):
        inside = feed_entry(
            "新制度を公表",
            link="https://www.digital.go.jp/news/story",
            tags=[{"term": "報道発表"}],
            published_parsed=time.gmtime(1_700_000_000),
        )
        outside = feed_entry(
            "過去の発表",
            link="https://www.digital.go.jp/news/old-story",
            tags=[{"term": "報道発表"}],
            published_parsed=time.gmtime(1_699_900_000),
        )
        config = feed_config(
            allowedHosts=["www.digital.go.jp"],
            includeEntryCategories=["報道発表"],
            fetchLinkedHtml=True,
            linkedHtmlParser="official",
            linkedHtmlRequired=True,
            linkedHtmlPathPrefixes=["/news/"],
            linkedHtmlMinimumChars=80,
            primarySource=True,
        )
        start = datetime.fromtimestamp(1_699_999_000, tz=UTC)
        end = datetime.fromtimestamp(1_700_001_000, tz=UTC)
        with patch(
            "scripts.news_pipeline.feeds._fetch_one", return_value=[inside, outside]
        ), patch(
            "scripts.news_pipeline.feeds._fetch_linked_html",
            return_value=OFFICIAL_HTML,
        ) as fetch_detail:
            candidates, failures = collect_candidates(
                [config], start, end, grace_hours=0, max_workers=1
            )
        self.assertEqual(failures, [])
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].primary_source)
        self.assertIn("政府は8月15日", candidates[0].description)
        fetch_detail.assert_called_once()

    def test_required_official_linked_html_failure_does_not_fall_back_to_headline(self):
        inside = feed_entry(
            "新制度を公表",
            link="https://www.digital.go.jp/news/story",
            published_parsed=time.gmtime(1_700_000_000),
        )
        config = feed_config(
            allowedHosts=["www.digital.go.jp"],
            fetchLinkedHtml=True,
            linkedHtmlParser="official",
            linkedHtmlRequired=True,
            linkedHtmlPathPrefixes=["/news/"],
        )
        start = datetime.fromtimestamp(1_699_999_000, tz=UTC)
        end = datetime.fromtimestamp(1_700_001_000, tz=UTC)
        with patch(
            "scripts.news_pipeline.feeds._fetch_one", return_value=[inside]
        ), patch(
            "scripts.news_pipeline.feeds._fetch_linked_html",
            side_effect=RuntimeError("unavailable"),
        ):
            candidates, failures = collect_candidates(
                [config], start, end, grace_hours=0, max_workers=1
            )
        self.assertEqual(candidates, [])
        self.assertEqual(failures, ["テスト配信"])

    def test_same_jma_event_id_collapses_to_richer_later_product(self):
        bulletin = feed_entry(
            "震度速報",
            link="https://www.data.jma.go.jp/data/serial-1.xml",
            published_parsed=time.gmtime(1_700_000_000),
        )
        detailed = feed_entry(
            "震源・震度に関する情報",
            link="https://www.data.jma.go.jp/data/serial-2.xml",
            published_parsed=time.gmtime(1_700_000_060),
        )
        config = feed_config(
            publisher="jma",
            allowedHosts=["data.jma.go.jp"],
            fetchLinkedXml=True,
            linkedXmlParser="jma",
            linkedXmlRequired=True,
            minimumMaxIntensity=3,
        )
        start = datetime.fromtimestamp(1_699_999_000, tz=UTC)
        end = datetime.fromtimestamp(1_700_001_000, tz=UTC)

        def linked_payload(url, _config):
            return EARTHQUAKE_XML if "serial-2" in url else INTENSITY_BULLETIN_XML

        with patch(
            "scripts.news_pipeline.feeds._fetch_one",
            return_value=[bulletin, detailed],
        ), patch(
            "scripts.news_pipeline.feeds._fetch_linked_xml",
            side_effect=linked_payload,
        ):
            candidates, failures = collect_candidates(
                [config], start, end, grace_hours=0, max_workers=2
            )
        self.assertEqual(failures, [])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].title, "石川県能登地方で地震 M4.2 最大震度4"
        )

    def test_jma_without_event_id_collapses_exact_final_title_to_latest(self):
        older = feed_entry(
            "土砂災害警戒情報",
            link="https://www.data.jma.go.jp/data/warning-old.xml",
            published_parsed=time.gmtime(1_700_000_000),
        )
        latest = feed_entry(
            "土砂災害警戒情報",
            link="https://www.data.jma.go.jp/data/warning-latest.xml",
            published_parsed=time.gmtime(1_700_000_120),
        )
        config = feed_config(
            publisher="jma",
            allowedHosts=["data.jma.go.jp"],
            fetchLinkedXml=True,
            linkedXmlParser="jma",
            linkedXmlRequired=True,
        )
        start = datetime.fromtimestamp(1_699_999_000, tz=UTC)
        end = datetime.fromtimestamp(1_700_001_000, tz=UTC)
        with patch(
            "scripts.news_pipeline.feeds._fetch_one", return_value=[older, latest]
        ), patch(
            "scripts.news_pipeline.feeds._fetch_linked_xml",
            return_value=SPECIFIC_WARNING_XML,
        ):
            candidates, failures = collect_candidates(
                [config], start, end, grace_hours=0, max_workers=2
            )
        self.assertEqual(failures, [])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].url, latest["link"])

    def test_category_inference_is_enabled_only_for_mixed_general_feeds(self):
        feeds = load_feed_config(ROOT / "config" / "feeds.json")
        enabled = {item["id"] for item in feeds if item.get("inferCategory")}
        self.assertEqual(
            enabled,
            {
                "nhk-top",
                "mainichi-flash",
                "asahi-headlines",
                "nhk-science-culture",
                "itmedia-news",
                "gov-online",
            },
        )
        by_id = {item["id"]: item for item in feeds}
        self.assertEqual(by_id["meti-releases"]["category"], "経済")
        self.assertEqual(by_id["mof-policy"]["category"], "経済")
        self.assertEqual(by_id["maff-releases"]["category"], "経済")
        self.assertEqual(by_id["nhk-science-culture"]["category"], "科学")
        for identifier in (
            "meti-releases",
            "mof-policy",
            "maff-releases",
            "jma-extra",
            "jma-eqvol",
            "nhk-world",
            "nhk-sports",
        ):
            self.assertNotIn("inferCategory", by_id[identifier])

    def test_new_primary_feeds_are_configured(self):
        feeds = load_feed_config(ROOT / "config" / "feeds.json")
        by_id = {item["id"]: item for item in feeds}
        for identifier in ("jma-extra", "jma-eqvol"):
            item = by_id[identifier]
            self.assertEqual(item["publisher"], "jma")
            self.assertIn(item["category"], {"国内", "経済"})
            self.assertEqual(item["priority"], 5)
            self.assertTrue(item["primarySource"])
            self.assertEqual(item["maxFeedBytes"], 8_000_000)
            self.assertTrue(item["extractAtomContent"])
            self.assertTrue(item["titleFromDescriptionBrackets"])
            self.assertTrue(item["fetchLinkedXml"])
            self.assertEqual(item["linkedXmlParser"], "jma")
            self.assertTrue(item["linkedXmlRequired"])
            self.assertEqual(item["linkedXmlMaxBytes"], 2_000_000)
            self.assertEqual(item["linkedXmlTimeoutSeconds"], 8)
            self.assertIn("data.jma.go.jp", item["allowedHosts"])
        self.assertIn("突風", by_id["jma-extra"]["includeTitlePatterns"])
        self.assertIn(
            "土砂災害(?:警戒|危険)",
            by_id["jma-extra"]["linkedXmlIncludePatterns"],
        )
        self.assertEqual(by_id["jma-eqvol"]["minimumMaxIntensity"], 3)
        self.assertIn(
            "^降灰予報（詳細）$",
            by_id["jma-eqvol"]["includeProductTitlePatterns"],
        )
        self.assertIn(
            "^降灰予報（定時）$",
            by_id["jma-eqvol"]["excludeProductTitlePatterns"],
        )
        detailed_ash = feed_entry(
            "降灰予報（詳細）",
            content=[{"value": "【降灰予報（詳細）】桜島の降灰予報です。"}],
        )
        routine_ash = feed_entry(
            "降灰予報（定時）",
            content=[{"value": "【降灰予報（定時）】定時の降灰予報です。"}],
        )
        self.assertIsNotNone(_entry_to_candidate(detailed_ash, by_id["jma-eqvol"]))
        self.assertIsNone(_entry_to_candidate(routine_ash, by_id["jma-eqvol"]))
        mofa = by_id["mofa-safety"]
        self.assertEqual(mofa["publisher"], "mofa")
        self.assertEqual(mofa["category"], "海外")
        self.assertEqual(mofa["priority"], 5)
        self.assertTrue(mofa["primarySource"])
        self.assertEqual(mofa["allowedHosts"], ["anzen.mofa.go.jp"])

        government_online = by_id["gov-online"]
        self.assertEqual(government_online["publisher"], "gov-online")
        self.assertEqual(government_online["category"], "国内")
        self.assertEqual(government_online["priority"], 5)
        self.assertTrue(government_online["primarySource"])
        self.assertTrue(government_online["inferCategory"])
        self.assertTrue(government_online["fetchLinkedHtml"])
        self.assertEqual(government_online["linkedHtmlParser"], "official")
        self.assertTrue(government_online["linkedHtmlRequired"])
        self.assertEqual(government_online["linkedHtmlRootIds"], ["main"])
        self.assertEqual(
            government_online["linkedHtmlPathPrefixes"], ["/article/"]
        )
        self.assertTrue(government_online["linkedHtmlRequireExactHost"])
        self.assertEqual(government_online["linkedHtmlMaxBytes"], 750_000)
        self.assertEqual(government_online["linkedHtmlTimeoutSeconds"], 8)
        self.assertEqual(government_online["linkedHtmlMinimumChars"], 240)
        self.assertIn("募集(?:します|を開始|について)?", government_online["excludeTitlePatterns"])
        self.assertIn("イベント・募集", government_online["excludeEntryCategories"])

        meti = by_id["meti-releases"]
        self.assertEqual(meti["publisher"], "meti")
        self.assertEqual(meti["category"], "経済")
        self.assertEqual(meti["priority"], 5)
        self.assertTrue(meti["primarySource"])
        self.assertNotIn("fetchLinkedHtml", meti)

        for identifier, expected_prefix in (
            ("maff-releases", "/j/press/"),
            ("digital-agency-releases", "/news/"),
        ):
            item = by_id[identifier]
            self.assertEqual(item["priority"], 5)
            self.assertTrue(item["primarySource"])
            self.assertTrue(item["fetchLinkedHtml"])
            self.assertEqual(item["linkedHtmlParser"], "official")
            self.assertTrue(item["linkedHtmlRequired"])
            self.assertEqual(item["linkedHtmlPathPrefixes"], [expected_prefix])
            self.assertEqual(item["linkedHtmlMaxBytes"], 750_000)
            self.assertEqual(item["linkedHtmlTimeoutSeconds"], 8)
            self.assertEqual(item["linkedHtmlMinimumChars"], 160)
        self.assertEqual(
            by_id["digital-agency-releases"]["includeEntryCategories"],
            ["報道発表"],
        )
        self.assertEqual(
            by_id["maff-releases"]["linkedHtmlRootIds"], ["main_content"]
        )
        mof = by_id["mof-policy"]
        self.assertEqual(mof["publisher"], "mof")
        self.assertEqual(mof["category"], "経済")
        self.assertTrue(mof["primarySource"])
        self.assertTrue(mof["fetchLinkedHtml"])
        self.assertEqual(mof["linkedHtmlRootIds"], ["main"])
        self.assertIn("/policy/", mof["linkedHtmlPathPrefixes"])
        self.assertIn("入札", mof["excludeTitlePatterns"])


if __name__ == "__main__":
    unittest.main()
