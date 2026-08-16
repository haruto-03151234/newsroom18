from __future__ import annotations

import calendar
import gzip
import io
import json
import logging
import re
import socket
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import feedparser

from .models import CATEGORIES, Candidate
from .text_utils import canonical_url, clean_text, normalize_title, stable_hash


LOGGER = logging.getLogger(__name__)
USER_AGENT = "NewsBriefJP/1.0 (+https://github.com/; feed reader)"
HTML_USER_AGENT = "Mozilla/5.0 (compatible; NewsBriefJP/1.0; +https://github.com/)"
DEFAULT_MAX_FEED_BYTES = 2_000_000
MAX_FEED_BYTES = 8_000_000
DEFAULT_LINKED_XML_MAX_BYTES = 750_000
MAX_LINKED_XML_BYTES = 2_000_000
DEFAULT_LINKED_XML_TIMEOUT_SECONDS = 8
MAX_LINKED_XML_TIMEOUT_SECONDS = 20
DEFAULT_LINKED_HTML_MAX_BYTES = 750_000
MAX_LINKED_HTML_BYTES = 2_000_000
DEFAULT_LINKED_HTML_TIMEOUT_SECONDS = 8
MAX_LINKED_HTML_TIMEOUT_SECONDS = 20
DEFAULT_LINKED_HTML_MINIMUM_CHARS = 160
MAX_LINKED_HTML_TEXT_CHARS = 900
_PATTERN_FIELDS = (
    "includeTitlePatterns",
    "excludeTitlePatterns",
    "includeProductTitlePatterns",
    "excludeProductTitlePatterns",
    "linkedXmlIncludePatterns",
    "linkedXmlExcludePatterns",
    "minimumMaxIntensityExemptPatterns",
)
_ENTRY_CATEGORY_FIELDS = ("includeEntryCategories", "excludeEntryCategories")
_LINKED_HTML_IGNORED_TAGS = {
    "aside",
    "audio",
    "button",
    "canvas",
    "footer",
    "form",
    "header",
    "iframe",
    "nav",
    "noscript",
    "script",
    "style",
    "svg",
    "template",
    "video",
}
_LINKED_HTML_BLOCK_TAGS = {"p", "li", "td", "dd", "dt"}
_HTML_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_LINKED_HTML_BOILERPLATE = re.compile(
    r"^(?:お?問い合わせ先|お?問合せ先|連絡先|電話\s*[:：]|TEL\s*[:：]|"
    r"FAX\s*[:：]|〒\d{3}|PDF形式のファイル|このページの先頭へ|"
    r"ページトップへ|シェアする|番組放送後|配信期間は|放送日\s|"
    r"再生時間\s|配信終了予定日\s|音声が再生されない場合|"
    r"動画が表示されない場合)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _JmaDetail:
    title: str
    description: str
    product_title: str
    max_intensity: str = ""
    event_id: str = ""
    serial: str = ""


@dataclass(frozen=True)
class _LinkedCandidate:
    candidate: Candidate
    event_id: str
    serial: str
    richness: int


class _ValidatedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(
        self,
        allowed_hosts: set[str],
        *,
        require_https: bool = False,
        allowed_path_prefixes: tuple[str, ...] = (),
        allow_subdomains: bool = True,
    ) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts
        self.require_https = require_https
        self.allowed_path_prefixes = allowed_path_prefixes
        self.allow_subdomains = allow_subdomains

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        canonical_url(newurl)
        parsed = urlsplit(newurl)
        if self.require_https and parsed.scheme.lower() != "https":
            raise urllib.error.HTTPError(newurl, code, "HTTPS redirect required", headers, fp)
        host = (parsed.hostname or "").lower()
        if not _host_is_allowed(
            host, self.allowed_hosts, allow_subdomains=self.allow_subdomains
        ):
            raise urllib.error.HTTPError(newurl, code, "redirect host is not allowlisted", headers, fp)
        if self.allowed_path_prefixes and not any(
            parsed.path.startswith(prefix) for prefix in self.allowed_path_prefixes
        ):
            raise urllib.error.HTTPError(newurl, code, "redirect path is not allowlisted", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl.strip())


class _OfficialHtmlExtractor(HTMLParser):
    """Extract paragraph-shaped text from an explicitly configured official page."""

    def __init__(self, root_ids: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.root_ids = root_ids
        self.root_depth = 0
        self.article_depth = 0
        self.main_depth = 0
        self.ignored_depth = 0
        self.stack: list[tuple[str, bool, bool, bool, bool]] = []
        self.block_depth = 0
        self.block_scope = ""
        self.block_parts: list[str] = []
        self.values: dict[str, list[str]] = {"root": [], "article": [], "main": []}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _HTML_VOID_TAGS:
            return
        attributes = {key.lower(): value or "" for key, value in attrs}
        enters_root = bool(self.root_ids and attributes.get("id") in self.root_ids)
        enters_article = tag == "article"
        enters_main = tag == "main"
        enters_ignored = tag in _LINKED_HTML_IGNORED_TAGS
        self.stack.append(
            (tag, enters_root, enters_article, enters_main, enters_ignored)
        )
        self.root_depth += int(enters_root)
        self.article_depth += int(enters_article)
        self.main_depth += int(enters_main)
        self.ignored_depth += int(enters_ignored)
        if (
            tag in _LINKED_HTML_BLOCK_TAGS
            and self.ignored_depth == 0
            and (self.root_depth or self.article_depth or self.main_depth)
        ):
            if self.block_depth == 0:
                self.block_scope = (
                    "root"
                    if self.root_depth
                    else "article"
                    if self.article_depth
                    else "main"
                )
                self.block_parts = []
            self.block_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_data(self, data: str) -> None:
        if self.block_depth and self.ignored_depth == 0:
            self.block_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _LINKED_HTML_BLOCK_TAGS and self.block_depth:
            self.block_depth -= 1
            if self.block_depth == 0:
                value = clean_text(" ".join(self.block_parts), 1_500)
                if value:
                    self.values[self.block_scope].append(value)
                self.block_scope = ""
                self.block_parts = []
        matching_index = next(
            (
                index
                for index in range(len(self.stack) - 1, -1, -1)
                if self.stack[index][0] == tag
            ),
            None,
        )
        if matching_index is None:
            return
        while len(self.stack) > matching_index:
            _, enters_root, enters_article, enters_main, enters_ignored = self.stack.pop()
            self.root_depth -= int(enters_root)
            self.article_depth -= int(enters_article)
            self.main_depth -= int(enters_main)
            self.ignored_depth -= int(enters_ignored)


def load_feed_config(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    feeds = raw.get("feeds", [])
    if not isinstance(feeds, list) or not feeds:
        raise ValueError("feed config must contain a non-empty feeds array")
    for item in feeds:
        if item.get("category") not in CATEGORIES:
            raise ValueError(f"unsupported category: {item.get('category')}")
        if "inferCategory" in item and not isinstance(item["inferCategory"], bool):
            raise ValueError("inferCategory must be a boolean")
        if "linkedHtmlRequireExactHost" in item and not isinstance(
            item["linkedHtmlRequireExactHost"], bool
        ):
            raise ValueError("linkedHtmlRequireExactHost must be a boolean")
        canonical_url(str(item["url"]))
        _feed_byte_limit(item)
        for field in _PATTERN_FIELDS:
            _validate_patterns(item.get(field), field)
        for field in _ENTRY_CATEGORY_FIELDS:
            _validate_string_array(item.get(field), field, maximum=32, item_limit=120)
        if item.get("fetchLinkedXml") and item.get("fetchLinkedHtml"):
            raise ValueError("a feed cannot enable both linked XML and linked HTML")
        if item.get("fetchLinkedXml"):
            if item.get("linkedXmlParser") != "jma":
                raise ValueError("fetchLinkedXml requires linkedXmlParser='jma'")
            _linked_xml_byte_limit(item)
            _linked_xml_timeout(item)
            if not item.get("allowedHosts"):
                raise ValueError("fetchLinkedXml requires allowedHosts")
            minimum_intensity = item.get("minimumMaxIntensity")
            if minimum_intensity is not None and _intensity_rank(
                str(minimum_intensity)
            ) is None:
                raise ValueError("minimumMaxIntensity is not a JMA intensity")
        if item.get("fetchLinkedHtml"):
            if item.get("linkedHtmlParser") != "official":
                raise ValueError(
                    "fetchLinkedHtml requires linkedHtmlParser='official'"
                )
            _linked_html_byte_limit(item)
            _linked_html_timeout(item)
            _linked_html_minimum_chars(item)
            if not item.get("allowedHosts"):
                raise ValueError("fetchLinkedHtml requires allowedHosts")
            root_ids = _validate_string_array(
                item.get("linkedHtmlRootIds"),
                "linkedHtmlRootIds",
                maximum=8,
                item_limit=100,
            )
            path_prefixes = _validate_string_array(
                item.get("linkedHtmlPathPrefixes"),
                "linkedHtmlPathPrefixes",
                maximum=8,
                item_limit=200,
            )
            if not path_prefixes or any(
                not prefix.startswith("/") or "?" in prefix or "#" in prefix
                for prefix in path_prefixes
            ):
                raise ValueError(
                    "fetchLinkedHtml requires absolute linkedHtmlPathPrefixes"
                )
    return feeds


def _feed_byte_limit(config: dict[str, Any]) -> int:
    raw = config.get("maxFeedBytes", DEFAULT_MAX_FEED_BYTES)
    if isinstance(raw, bool):
        raise ValueError("maxFeedBytes must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("maxFeedBytes must be an integer") from exc
    if value < 1 or value > MAX_FEED_BYTES:
        raise ValueError(f"maxFeedBytes must be between 1 and {MAX_FEED_BYTES}")
    return value


def _validate_patterns(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError(f"{field} must be an array with at most 32 patterns")
    patterns: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or not raw or len(raw) > 200:
            raise ValueError(f"{field} contains an invalid pattern")
        try:
            re.compile(raw, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"{field} contains an invalid regular expression") from exc
        patterns.append(raw)
    return patterns


def _validate_string_array(
    value: Any, field: str, *, maximum: int, item_limit: int
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} must be an array with at most {maximum} values")
    values: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or not raw.strip() or len(raw) > item_limit:
            raise ValueError(f"{field} contains an invalid value")
        normalized = raw.strip()
        if normalized not in values:
            values.append(normalized)
    return values


def _linked_xml_byte_limit(config: dict[str, Any]) -> int:
    return _bounded_config_int(
        config,
        "linkedXmlMaxBytes",
        DEFAULT_LINKED_XML_MAX_BYTES,
        MAX_LINKED_XML_BYTES,
    )


def _linked_xml_timeout(config: dict[str, Any]) -> int:
    return _bounded_config_int(
        config,
        "linkedXmlTimeoutSeconds",
        DEFAULT_LINKED_XML_TIMEOUT_SECONDS,
        MAX_LINKED_XML_TIMEOUT_SECONDS,
    )


def _linked_html_byte_limit(config: dict[str, Any]) -> int:
    return _bounded_config_int(
        config,
        "linkedHtmlMaxBytes",
        DEFAULT_LINKED_HTML_MAX_BYTES,
        MAX_LINKED_HTML_BYTES,
    )


def _linked_html_timeout(config: dict[str, Any]) -> int:
    return _bounded_config_int(
        config,
        "linkedHtmlTimeoutSeconds",
        DEFAULT_LINKED_HTML_TIMEOUT_SECONDS,
        MAX_LINKED_HTML_TIMEOUT_SECONDS,
    )


def _linked_html_minimum_chars(config: dict[str, Any]) -> int:
    return _bounded_config_int(
        config,
        "linkedHtmlMinimumChars",
        DEFAULT_LINKED_HTML_MINIMUM_CHARS,
        MAX_LINKED_HTML_TEXT_CHARS,
    )


def _bounded_config_int(
    config: dict[str, Any], field: str, default: int, maximum: int
) -> int:
    raw = config.get(field, default)
    if isinstance(raw, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if value < 1 or value > maximum:
        raise ValueError(f"{field} must be between 1 and {maximum}")
    return value


def collect_candidates(
    feeds: list[dict[str, Any]],
    start: datetime,
    end: datetime,
    grace_hours: float = 1.5,
    max_workers: int = 8,
) -> tuple[list[Candidate], list[str]]:
    candidates: list[Candidate] = []
    linked_candidates: list[tuple[Candidate, Any, dict[str, Any]]] = []
    failures: list[str] = []
    earliest = start.timestamp() - grace_hours * 3600
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, item): item for item in feeds}
        for future in as_completed(futures):
            item = futures[future]
            try:
                entries = future.result()
            except Exception as exc:
                LOGGER.warning("Feed failed: %s: %s", item.get("name"), exc)
                failures.append(str(item.get("name", item.get("url"))))
                continue
            for entry in entries:
                candidate = _entry_to_candidate(entry, item)
                if not candidate:
                    continue
                if earliest <= candidate.published_at.timestamp() < end.timestamp():
                    if item.get("fetchLinkedXml") or item.get("fetchLinkedHtml"):
                        linked_candidates.append((candidate, entry, item))
                    else:
                        candidates.append(candidate)

    linked_records: list[_LinkedCandidate] = []
    if linked_candidates:
        detail_workers = max(1, min(max_workers, len(linked_candidates)))
        with ThreadPoolExecutor(max_workers=detail_workers) as executor:
            futures = {
                executor.submit(_enrich_linked_candidate_record, candidate, entry, item): (
                    candidate,
                    item,
                )
                for candidate, entry, item in linked_candidates
            }
            for future in as_completed(futures):
                candidate, item = futures[future]
                try:
                    enriched = future.result()
                except Exception as exc:
                    LOGGER.warning("Linked detail failed: %s: %s", item.get("name"), exc)
                    label = str(item.get("name", item.get("url")))
                    if label not in failures:
                        failures.append(label)
                    if not _linked_detail_required(item):
                        candidates.append(candidate)
                    continue
                if enriched is not None:
                    linked_records.append(enriched)
    candidates.extend(_collapse_linked_candidates(linked_records))
    candidates.sort(key=lambda c: (c.priority, c.published_at), reverse=True)
    return candidates, failures


def _fetch_one(config: dict[str, Any]) -> list[Any]:
    byte_limit = _feed_byte_limit(config)
    raw_feed_url = str(config["url"]).strip()
    feed_url = canonical_url(raw_feed_url)
    if urlsplit(raw_feed_url).path.endswith("/") and not urlsplit(feed_url).path.endswith("/"):
        parsed = urlsplit(feed_url)
        feed_url = parsed._replace(path=parsed.path + "/").geturl()
    configured_host = (urlsplit(feed_url).hostname or "").lower()
    allowed_hosts = {str(value).lower().rstrip(".") for value in config.get("allowedHosts", [])}
    allowed_hosts.add(configured_host)
    request = urllib.request.Request(
        feed_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml",
            "Accept-Encoding": "identity",
        },
    )
    try:
        opener = urllib.request.build_opener(_ValidatedRedirect(allowed_hosts))
        with opener.open(request, timeout=20) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > byte_limit:
                raise RuntimeError("feed exceeds size limit")
            payload = response.read(byte_limit + 1)
            content_encoding = response.headers.get("Content-Encoding", "").lower()
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"unable to retrieve feed: {exc}") from exc
    if len(payload) > byte_limit:
        raise RuntimeError("feed exceeds size limit")
    if "gzip" in content_encoding or payload.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
                payload = compressed.read(byte_limit + 1)
        except OSError as exc:
            raise RuntimeError("feed gzip payload is invalid") from exc
        if len(payload) > byte_limit:
            raise RuntimeError("decompressed feed exceeds size limit")
    upper = payload[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise RuntimeError("feed contains a disallowed document type")
    parsed = feedparser.parse(payload)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"invalid feed: {parsed.bozo_exception}")
    return list(parsed.entries)


def _entry_to_candidate(entry: Any, config: dict[str, Any]) -> Candidate | None:
    if not _entry_categories_are_allowed(entry, config):
        return None
    original_title = clean_text(entry.get("title"), 260)
    title = original_title
    raw_description = ""
    if not config.get("metadataOnly") or config.get("titleFromDescriptionBrackets"):
        raw_description = _entry_description(entry, config)
    if config.get("titleFromDescriptionBrackets"):
        derived_title = _description_bracket_title(raw_description)
        if derived_title:
            title = derived_title
    if not _title_is_allowed(title, config, original_title=original_title):
        return None
    raw_url = entry.get("link") or entry.get("id")
    if not title or not raw_url:
        return None
    raw_url = str(raw_url)
    if config.get("forceHttps") and raw_url.startswith("http://"):
        raw_url = "https://" + raw_url.removeprefix("http://")
    try:
        url = canonical_url(raw_url)
    except ValueError:
        return None
    if config.get("fetchLinkedHtml"):
        try:
            _validated_linked_html_url(url, config)
        except RuntimeError:
            return None
    parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed_time:
        return None
    published = datetime.fromtimestamp(calendar.timegm(parsed_time), tz=UTC)
    description = "" if config.get("metadataOnly") else raw_description
    identity = stable_hash(f"{config['name']}|{url}|{published.isoformat()}", 18)
    return Candidate(
        id=identity,
        title=title,
        description=description,
        url=url,
        source_name=clean_text(str(config["name"]), 80),
        category=_infer_entry_category(entry, title, raw_description, config),
        published_at=published,
        priority=max(1, min(5, int(config.get("priority", 3)))),
        ai_required=bool(config.get("aiRequired", False)),
        primary_source=bool(config.get("primarySource", False)),
        publisher_id=clean_text(str(config.get("publisher", config.get("id", config["name"]))), 60),
    )


_INFERRED_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "スポーツ",
        re.compile(
            r"野球|甲子園|大リーグ|プロ野球|投手|球団|サッカー|Jリーグ|"
            r"ワールドカップ|五輪|オリンピック|パラリンピック|陸上|"
            r"テニス|ゴルフ|相撲|競泳|バスケット|バレー|ラグビー|"
            r"(?:^|[^a-z])F1(?:[^a-z]|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "エンタメ",
        re.compile(
            r"映画|ドラマ|俳優|女優|芸能|音楽|マンガ|漫画|アニメ|ゲーム|"
            r"ホラー|怪談|妖怪|舞台|小説|文学|美術|展覧会|アイドル|歌手",
            re.IGNORECASE,
        ),
    ),
    (
        "海外",
        re.compile(
            r"ウクライナ|ロシア|インドネシア|北朝鮮|韓国(?!籍)|"
            r"中国(?!地方|籍)|台湾|香港|米国|アメリカ|英国(?!籍)|イギリス|"
            r"フランス|ドイツ|欧州|(?:^|[^a-z])EU(?:[^a-z]|$)|国連|"
            r"ガザ|イスラエル|パレスチナ|イラン|シリア|アフリカ|"
            r"アフガニスタン|アフガン|タリバン|リヒテンシュタイン|"
            r"エジプト|フィリピン|ブラジル|インド|海外",
            re.IGNORECASE,
        ),
    ),
    (
        "テクノロジー",
        re.compile(
            r"(?:^|[^a-z])AI(?:[^a-z]|$)|人工知能|生成AI|Claude|ChatGPT|"
            r"サイバー|ソフトウェア|アプリ|スマホ|iPhone|クラウド|半導体|"
            r"ロボット|デジタル|(?:^|[^a-z])IT(?:[^a-z]|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "経済",
        re.compile(
            r"物価|価格|高騰|値上げ|値下げ|株価|株式|為替|円相場|金利|景気|"
            r"経済|企業|決算|市場|賃金|給与|雇用|失業|輸出|輸入|関税|"
            r"金融|銀行|投資|買収|売却|倒産|(?:^|[^a-z])GDP(?:[^a-z]|$)|"
            r"プロテイン",
            re.IGNORECASE,
        ),
    ),
    (
        "科学",
        re.compile(
            r"宇宙|科学|研究|医療|感染症|ワクチン|新型コロナ|治療|患者|"
            r"気候変動|生物|化石|天文|探査機|衛星",
            re.IGNORECASE,
        ),
    ),
    (
        "社会",
        re.compile(
            r"事故|火災|逮捕|事件|容疑|裁判|判決|軽傷|重傷|けが|死亡|"
            r"災害|地震|大雨|警報|避難|教育|学校|給食|自治体",
            re.IGNORECASE,
        ),
    ),
)

_INFERRED_TAG_CATEGORIES = {
    "社会": "社会",
    "スポーツ": "スポーツ",
    "ビジネス": "経済",
    "経済": "経済",
    "政治": "国内",
    "国際": "海外",
    "海外": "海外",
    "サイエンス": "科学",
    "科学": "科学",
    "文化芸能": "エンタメ",
    "エンタメ": "エンタメ",
    "テクノロジー": "テクノロジー",
    "デジタル化": "テクノロジー",
    "サイバーセキュリティ": "テクノロジー",
    "it・デジタル": "テクノロジー",
    "it・デジタルのトラブル": "テクノロジー",
    "労働": "経済",
    "経営": "経済",
    "税": "経済",
    "観光": "経済",
    "エネルギー": "経済",
    "経済・労働・税": "経済",
    "防犯": "社会",
    "交通安全": "社会",
    "防災・災害対策": "社会",
    "安心・安全（その他）": "社会",
    "教育・学び": "社会",
    "健康": "科学",
    "医療": "科学",
    "病気予防": "科学",
    "環境・エコ": "科学",
}


def _infer_entry_category(
    entry: Any,
    title: str,
    description: str,
    config: dict[str, Any],
) -> str:
    """Conservatively refine broad mixed-feed categories from supplied metadata."""
    default = str(config["category"])
    if not config.get("inferCategory"):
        return default

    tag_values = _entry_tag_values(entry)
    for tag in tag_values:
        mapped = _INFERRED_TAG_CATEGORIES.get(
            unicodedata.normalize("NFKC", tag).casefold()
        )
        if mapped:
            return mapped

    values = [title, description, *tag_values]
    haystack = unicodedata.normalize("NFKC", " ".join(values))
    for category, pattern in _INFERRED_CATEGORY_PATTERNS:
        if pattern.search(haystack):
            return category
    return default


def _entry_tag_values(entry: Any) -> list[str]:
    values: list[str] = []
    raw_tags = entry.get("tags") or []
    if isinstance(raw_tags, dict):
        raw_tags = [raw_tags]
    if isinstance(raw_tags, list):
        for item in raw_tags:
            if isinstance(item, dict):
                raw = item.get("term") or item.get("label")
            else:
                raw = item
            value = clean_text(str(raw) if raw is not None else "", 120)
            # RSS 1.0 publishers sometimes serialize several subjects in one
            # dc:subject value.  Treat those supplied subjects as individual
            # tags so category inference and opt-in noise filters can use them.
            for part in re.split(r"[,、]", value):
                normalized = clean_text(part, 120)
                if normalized and normalized not in values:
                    values.append(normalized)
    return values


def _entry_description(entry: Any, config: dict[str, Any]) -> str:
    values: list[Any] = []
    if config.get("extractAtomContent"):
        content = entry.get("content")
        if isinstance(content, list):
            values.extend(
                item.get("value") for item in content if isinstance(item, dict)
            )
        elif isinstance(content, dict):
            values.append(content.get("value"))
    values.extend(
        (entry.get("summary"), entry.get("description"), entry.get("subtitle"))
    )
    for value in values:
        description = clean_text(str(value) if value is not None else "", 900)
        if description:
            return description
    return ""


def _description_bracket_title(description: str) -> str:
    match = re.match(r"^【\s*([^】]{1,180}?)\s*】", description)
    return clean_text(match.group(1), 180) if match else ""


def _title_is_allowed(
    title: str, config: dict[str, Any], *, original_title: str = ""
) -> bool:
    if not title and not original_title:
        return False
    if not _text_matches_config_patterns(
        title,
        config,
        include_field="includeTitlePatterns",
        exclude_field="excludeTitlePatterns",
    ):
        return False
    if not _text_matches_config_patterns(
        original_title,
        config,
        include_field="includeProductTitlePatterns",
        exclude_field="excludeProductTitlePatterns",
    ):
        return False
    return True


def _entry_categories_are_allowed(entry: Any, config: dict[str, Any]) -> bool:
    include = {
        unicodedata.normalize("NFKC", value).casefold()
        for value in _validate_string_array(
            config.get("includeEntryCategories"),
            "includeEntryCategories",
            maximum=32,
            item_limit=120,
        )
    }
    exclude = {
        unicodedata.normalize("NFKC", value).casefold()
        for value in _validate_string_array(
            config.get("excludeEntryCategories"),
            "excludeEntryCategories",
            maximum=32,
            item_limit=120,
        )
    }
    actual = {
        unicodedata.normalize("NFKC", value).casefold()
        for value in _entry_tag_values(entry)
    }
    if actual & exclude:
        return False
    return not include or bool(actual & include)


def _linked_detail_required(config: dict[str, Any]) -> bool:
    if config.get("fetchLinkedHtml"):
        return bool(config.get("linkedHtmlRequired", False))
    return bool(config.get("linkedXmlRequired", False))


def _enrich_linked_candidate(
    candidate: Candidate, entry: Any, config: dict[str, Any]
) -> Candidate | None:
    record = _enrich_linked_candidate_record(candidate, entry, config)
    return record.candidate if record is not None else None


def _enrich_linked_candidate_record(
    candidate: Candidate, entry: Any, config: dict[str, Any]
) -> _LinkedCandidate | None:
    if config.get("fetchLinkedHtml"):
        if config.get("linkedHtmlParser") != "official":
            raise RuntimeError("unsupported linked HTML parser")
        payload = _fetch_linked_html(candidate.url, config)
        detail = _extract_official_html_detail(payload, config, candidate.title)
        if len(detail) < _linked_html_minimum_chars(config):
            raise RuntimeError("linked official HTML contained too little factual text")
        enriched = replace(candidate, description=detail)
        return _LinkedCandidate(
            candidate=enriched,
            event_id="",
            serial="",
            richness=len(detail),
        )
    if config.get("linkedXmlParser") != "jma":
        raise RuntimeError("unsupported linked XML parser")
    payload = _fetch_linked_xml(candidate.url, config)
    original_title = clean_text(entry.get("title"), 260)
    detail = _extract_jma_detail(payload, original_title)
    if not detail.description:
        raise RuntimeError("linked JMA XML contained no usable factual text")

    # Detail filters intentionally ignore the generic product title. For
    # example, "気象特別警報・警報・注意報" is also used for routine advisories.
    detail_text = " ".join(
        value for value in (detail.title, detail.description) if value
    )
    if not _text_matches_config_patterns(
        detail_text,
        config,
        include_field="linkedXmlIncludePatterns",
        exclude_field="linkedXmlExcludePatterns",
    ):
        return None

    minimum = config.get("minimumMaxIntensity")
    max_rank = _intensity_rank(detail.max_intensity)
    minimum_rank = _intensity_rank(str(minimum)) if minimum is not None else None
    product_identity = " ".join(
        value
        for value in (original_title, detail.product_title, candidate.title)
        if value
    )
    exempt_patterns = _validate_patterns(
        config.get("minimumMaxIntensityExemptPatterns"),
        "minimumMaxIntensityExemptPatterns",
    )
    is_exempt = any(
        re.search(pattern, product_identity, re.IGNORECASE)
        for pattern in exempt_patterns
    )
    if (
        max_rank is not None
        and minimum_rank is not None
        and max_rank < minimum_rank
        and not is_exempt
    ):
        return None

    enriched = replace(
        candidate, title=detail.title or candidate.title, description=detail.description
    )
    richness = len(detail.description)
    if re.search(r"\bM[0-9]", detail.title):
        richness += 300
    if detail.max_intensity:
        richness += 100
    if re.search(r"震源・震度", detail.product_title):
        richness += 200
    return _LinkedCandidate(
        candidate=enriched,
        event_id=detail.event_id,
        serial=detail.serial,
        richness=richness,
    )


def _collapse_linked_candidates(records: list[_LinkedCandidate]) -> list[Candidate]:
    selected: dict[tuple[str, str], _LinkedCandidate] = {}
    independent: list[Candidate] = []
    for record in records:
        publisher = record.candidate.publisher_id or record.candidate.source_name
        if publisher != "jma":
            independent.append(record.candidate)
            continue
        identity = (
            f"event:{record.event_id}"
            if record.event_id
            else f"title:{normalize_title(record.candidate.title)}"
        )
        if identity == "title:":
            independent.append(record.candidate)
            continue
        key = (publisher, identity)
        previous = selected.get(key)
        if previous is None or _linked_candidate_rank(record) > _linked_candidate_rank(
            previous
        ):
            selected[key] = record
    independent.extend(record.candidate for record in selected.values())
    return independent


def _linked_candidate_rank(record: _LinkedCandidate) -> tuple[Any, ...]:
    try:
        serial = int(record.serial)
    except (TypeError, ValueError):
        serial = -1
    # The newest bulletin must win for cancellations or downgraded warnings;
    # richness breaks ties between products issued at the same event time.
    return (record.candidate.published_at, serial, record.richness)


def _fetch_linked_xml(url: str, config: dict[str, Any]) -> bytes:
    try:
        linked_url = canonical_url(url)
    except ValueError as exc:
        raise RuntimeError("linked XML URL is invalid") from exc
    parsed = urlsplit(linked_url)
    if parsed.scheme != "https":
        raise RuntimeError("linked XML must use HTTPS")
    allowed_hosts = _configured_allowed_hosts(config)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not _host_is_allowed(host, allowed_hosts):
        raise RuntimeError("linked XML host is not allowlisted")

    byte_limit = _linked_xml_byte_limit(config)
    timeout = _linked_xml_timeout(config)
    request = urllib.request.Request(
        linked_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/xml, text/xml",
            "Accept-Encoding": "identity",
        },
    )
    try:
        opener = urllib.request.build_opener(
            _ValidatedRedirect(allowed_hosts, require_https=True)
        )
        with opener.open(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            try:
                if length and int(length) > byte_limit:
                    raise RuntimeError("linked XML exceeds size limit")
            except ValueError as exc:
                raise RuntimeError("linked XML has an invalid content length") from exc
            payload = response.read(byte_limit + 1)
            content_encoding = response.headers.get("Content-Encoding", "").lower()
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"unable to retrieve linked XML: {exc}") from exc
    if len(payload) > byte_limit:
        raise RuntimeError("linked XML exceeds size limit")
    if "gzip" in content_encoding or payload.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
                payload = compressed.read(byte_limit + 1)
        except OSError as exc:
            raise RuntimeError("linked XML gzip payload is invalid") from exc
        if len(payload) > byte_limit:
            raise RuntimeError("decompressed linked XML exceeds size limit")
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise RuntimeError("linked XML contains a disallowed document type")
    return payload


def _fetch_linked_html(url: str, config: dict[str, Any]) -> bytes:
    linked_url, allowed_hosts, path_prefixes = _validated_linked_html_url(url, config)
    byte_limit = _linked_html_byte_limit(config)
    timeout = _linked_html_timeout(config)
    request = urllib.request.Request(
        linked_url,
        headers={
            "User-Agent": HTML_USER_AGENT,
            "Accept": "text/html, application/xhtml+xml",
            "Accept-Encoding": "identity",
        },
    )
    try:
        opener = urllib.request.build_opener(
            _ValidatedRedirect(
                allowed_hosts,
                require_https=True,
                allowed_path_prefixes=path_prefixes,
                allow_subdomains=not bool(
                    config.get("linkedHtmlRequireExactHost", False)
                ),
            )
        )
        with opener.open(request, timeout=timeout) as response:
            content_type = str(response.headers.get("Content-Type", ""))
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type not in {"text/html", "application/xhtml+xml"}:
                raise RuntimeError("linked HTML response is not HTML")
            length = response.headers.get("Content-Length")
            try:
                if length and int(length) > byte_limit:
                    raise RuntimeError("linked HTML exceeds size limit")
            except ValueError as exc:
                raise RuntimeError("linked HTML has an invalid content length") from exc
            final_url = response.geturl()
            if isinstance(final_url, str):
                _validated_linked_html_url(final_url, config)
            payload = response.read(byte_limit + 1)
            content_encoding = response.headers.get("Content-Encoding", "").lower()
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"unable to retrieve linked HTML: {exc}") from exc
    if len(payload) > byte_limit:
        raise RuntimeError("linked HTML exceeds size limit")
    if "gzip" in content_encoding or payload.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
                payload = compressed.read(byte_limit + 1)
        except OSError as exc:
            raise RuntimeError("linked HTML gzip payload is invalid") from exc
        if len(payload) > byte_limit:
            raise RuntimeError("decompressed linked HTML exceeds size limit")
    return payload


def _validated_linked_html_url(
    url: str, config: dict[str, Any]
) -> tuple[str, set[str], tuple[str, ...]]:
    try:
        linked_url = canonical_url(url)
    except ValueError as exc:
        raise RuntimeError("linked HTML URL is invalid") from exc
    parsed = urlsplit(linked_url)
    if parsed.scheme != "https":
        raise RuntimeError("linked HTML must use HTTPS")
    allowed_hosts = _configured_allowed_hosts(config)
    host = (parsed.hostname or "").lower().rstrip(".")
    exact_host = bool(config.get("linkedHtmlRequireExactHost", False))
    if not _host_is_allowed(host, allowed_hosts, allow_subdomains=not exact_host):
        raise RuntimeError("linked HTML host is not allowlisted")
    path_prefixes = tuple(
        _validate_string_array(
            config.get("linkedHtmlPathPrefixes"),
            "linkedHtmlPathPrefixes",
            maximum=8,
            item_limit=200,
        )
    )
    if not path_prefixes or not any(
        parsed.path.startswith(prefix) for prefix in path_prefixes
    ):
        raise RuntimeError("linked HTML path is not allowlisted")
    suffix = Path(parsed.path).suffix.lower()
    if suffix and suffix not in {".html", ".htm"}:
        raise RuntimeError("linked HTML URL does not identify an HTML page")
    return linked_url, allowed_hosts, path_prefixes


def _extract_official_html_detail(
    payload: bytes, config: dict[str, Any], title: str = ""
) -> str:
    if not payload:
        return ""
    text = payload.decode("utf-8", errors="replace")
    root_ids = set(
        _validate_string_array(
            config.get("linkedHtmlRootIds"),
            "linkedHtmlRootIds",
            maximum=8,
            item_limit=100,
        )
    )
    parser = _OfficialHtmlExtractor(root_ids)
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise RuntimeError("linked official HTML is invalid") from exc
    values = parser.values["root"] or parser.values["article"] or parser.values["main"]
    normalized_title = normalize_title(title)
    selected: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = clean_text(raw, 1_500)
        value = re.sub(
            r"^(?:プレスリリース\s*)?(?:(?:X\s*ポスト|Tweet|印刷)\s*)+",
            "",
            value,
            flags=re.IGNORECASE,
        ).strip()
        if not value or _LINKED_HTML_BOILERPLATE.search(value):
            continue
        normalized = unicodedata.normalize("NFKC", value).casefold()
        compact = re.sub(r"\s+", "", normalized)
        if not compact or compact in seen:
            continue
        if normalized_title and normalize_title(value) == normalized_title:
            continue
        if re.fullmatch(r"(?:別紙|関連資料|参考資料|添付資料|概要)", value):
            continue
        seen.add(compact)
        selected.append(value)

    detail = " ".join(selected)
    if len(detail) <= MAX_LINKED_HTML_TEXT_CHARS:
        return detail
    clipped = detail[:MAX_LINKED_HTML_TEXT_CHARS]
    sentence_end = max(clipped.rfind("。"), clipped.rfind("！"), clipped.rfind("？"))
    if sentence_end >= DEFAULT_LINKED_HTML_MINIMUM_CHARS:
        return clipped[: sentence_end + 1].rstrip()
    return clipped.rstrip()


def _configured_allowed_hosts(config: dict[str, Any]) -> set[str]:
    hosts = {
        str(value).lower().rstrip(".")
        for value in config.get("allowedHosts", [])
        if str(value).strip()
    }
    configured_host = (urlsplit(str(config.get("url", ""))).hostname or "").lower()
    if configured_host:
        hosts.add(configured_host.rstrip("."))
    return hosts


def _host_is_allowed(
    host: str, allowed_hosts: set[str], *, allow_subdomains: bool = True
) -> bool:
    if not allow_subdomains:
        return host in allowed_hosts
    return any(
        host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts
    )


def _text_matches_config_patterns(
    value: str,
    config: dict[str, Any],
    *,
    include_field: str,
    exclude_field: str,
) -> bool:
    include = _validate_patterns(config.get(include_field), include_field)
    exclude = _validate_patterns(config.get(exclude_field), exclude_field)
    if any(re.search(pattern, value, re.IGNORECASE) for pattern in exclude):
        return False
    return not include or any(
        re.search(pattern, value, re.IGNORECASE) for pattern in include
    )


def _extract_jma_detail(payload: bytes, fallback_product_title: str = "") -> _JmaDetail:
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise RuntimeError("linked XML contains a disallowed document type")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RuntimeError("linked JMA XML is invalid") from exc

    product_title = (
        _first_direct_child_text(root, "Head", "Title")
        or _first_direct_child_text(root, "Control", "Title")
        or clean_text(fallback_product_title, 180)
    )
    event_id = _first_direct_child_text(root, "Head", "EventID")
    serial = _first_direct_child_text(root, "Head", "Serial")
    origin_time = _first_local_text(root, "OriginTime")
    hypocenter = _hypocenter_name(root)
    magnitude_element = _first_local_element(root, "Magnitude")
    magnitude_value = _element_text(magnitude_element) if magnitude_element is not None else ""
    magnitude_description = ""
    if magnitude_element is not None:
        magnitude_description = clean_text(
            magnitude_element.attrib.get("description", ""), 40
        )
    magnitude_display = magnitude_description or (
        f"M{magnitude_value}" if magnitude_value else ""
    )
    max_intensity = _first_local_text(root, "MaxInt")
    max_intensity_display = _display_intensity(max_intensity)
    headline_texts = _texts_under(root, "Headline", "Text")
    area_details = _official_area_details(root)
    tsunami_details = _tsunami_details(root)
    all_official_texts = _all_local_texts(root, "Text", limit=8)

    product_identity = " ".join(
        value for value in (fallback_product_title, product_title) if value
    )
    is_earthquake = bool(
        re.search(r"震源|震度|地震", product_identity)
        and not re.search(r"津波|噴火|火山", product_identity)
    )
    title = ""
    if is_earthquake and hypocenter and (magnitude_value or max_intensity):
        title_parts = [f"{hypocenter}で地震"]
        normalized_magnitude = _normalized_magnitude(magnitude_value, magnitude_description)
        if normalized_magnitude:
            title_parts.append(normalized_magnitude)
        if max_intensity_display:
            title_parts.append(f"最大震度{max_intensity_display}")
        title = clean_text(" ".join(title_parts), 100)
    elif product_title:
        title = clean_text(product_title, 100)
    elif headline_texts:
        title = _first_sentence(headline_texts[0], 100)

    details: list[str] = []
    if origin_time:
        details.append(f"発生時刻: {origin_time}")
    if hypocenter:
        details.append(f"震央・震源地域: {hypocenter}")
    if magnitude_display:
        details.append(f"マグニチュード: {magnitude_display}")
    if max_intensity_display:
        details.append(f"最大震度: {max_intensity_display}")
    if is_earthquake:
        # Put tsunami guidance before regional detail so the most important
        # safety conclusion remains visible even when the UI limits fact rows.
        details.extend(
            value
            for value in all_official_texts
            if re.search(r"津波|海面変動", value)
        )
    details.extend(headline_texts)
    details.extend(area_details)
    details.extend(tsunami_details)
    details.extend(all_official_texts)
    if not details and product_title:
        details.append(product_title)
    description = _join_official_details(details, 900)
    return _JmaDetail(
        title=title,
        description=description,
        product_title=product_title,
        max_intensity=max_intensity,
        event_id=event_id,
        serial=serial,
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _element_text(element: ET.Element | None, limit: int = 500) -> str:
    if element is None:
        return ""
    return clean_text(" ".join(element.itertext()), limit)


def _first_local_element(root: ET.Element, name: str) -> ET.Element | None:
    return next((element for element in root.iter() if _local_name(element.tag) == name), None)


def _first_local_text(root: ET.Element, name: str) -> str:
    return _element_text(_first_local_element(root, name), 180)


def _first_direct_child_text(
    root: ET.Element, parent_name: str, child_name: str
) -> str:
    for parent in root.iter():
        if _local_name(parent.tag) != parent_name:
            continue
        for child in parent:
            if _local_name(child.tag) == child_name:
                value = _element_text(child, 180)
                if value:
                    return value
    return ""


def _hypocenter_name(root: ET.Element) -> str:
    for hypocenter in root.iter():
        if _local_name(hypocenter.tag) != "Hypocenter":
            continue
        for area in hypocenter.iter():
            if _local_name(area.tag) != "Area":
                continue
            for element in area.iter():
                if _local_name(element.tag) == "Name":
                    value = _element_text(element, 100)
                    if value:
                        return value
    return ""


def _texts_under(root: ET.Element, parent_name: str, child_name: str) -> list[str]:
    values: list[str] = []
    for parent in root.iter():
        if _local_name(parent.tag) != parent_name:
            continue
        for element in parent.iter():
            if _local_name(element.tag) != child_name:
                continue
            value = _element_text(element)
            if value and value not in values:
                values.append(value)
    return values[:4]


def _all_local_texts(root: ET.Element, name: str, limit: int) -> list[str]:
    values: list[str] = []
    for element in root.iter():
        if _local_name(element.tag) != name:
            continue
        value = _element_text(element)
        if value and value not in values:
            values.append(value)
        if len(values) >= limit:
            break
    return values


def _official_area_details(root: ET.Element) -> list[str]:
    details: list[str] = []
    for item in root.iter():
        if _local_name(item.tag) != "Item":
            continue
        kinds, areas = _item_kinds_and_areas(item)
        if kinds and areas:
            value = f"{'、'.join(kinds)}: {'、'.join(areas)}"
            if value not in details:
                details.append(value)
        if len(details) >= 8:
            break
    return details


def _item_kinds_and_areas(item: ET.Element) -> tuple[list[str], list[str]]:
    kinds: list[str] = []
    areas: list[str] = []
    for element in item.iter():
        local = _local_name(element.tag)
        if local == "Kind":
            name = _first_local_text(element, "Name")
            if name and name not in kinds:
                kinds.append(name)
        elif local == "Area":
            name = _first_local_text(element, "Name")
            if name and name not in areas:
                areas.append(name)
    return kinds, areas


def _tsunami_details(root: ET.Element) -> list[str]:
    details: list[str] = []
    for tsunami in root.iter():
        if _local_name(tsunami.tag) != "Tsunami":
            continue
        for item in tsunami.iter():
            if _local_name(item.tag) != "Item":
                continue
            kinds, areas = _item_kinds_and_areas(item)
            height_element = _first_local_element(item, "TsunamiHeight")
            height = ""
            if height_element is not None:
                height = clean_text(
                    height_element.attrib.get("description", ""), 80
                ) or _element_text(height_element, 80)
            arrival = _first_local_text(item, "ArrivalTime")
            condition = _first_local_text(item, "Condition")
            parts: list[str] = []
            if kinds:
                parts.append("、".join(kinds))
            if areas:
                parts.append("対象地域 " + "、".join(areas))
            if height:
                parts.append("予想される最大波 " + height)
            if arrival:
                parts.append("到達予想時刻 " + arrival)
            if condition:
                parts.append(condition)
            if parts:
                value = " / ".join(parts)
                if value not in details:
                    details.append(value)
            if len(details) >= 8:
                return details
    return details


def _display_intensity(value: str) -> str:
    return {
        "5-": "5弱",
        "5+": "5強",
        "6-": "6弱",
        "6+": "6強",
    }.get(value, clean_text(value, 20))


def _intensity_rank(value: str) -> float | None:
    normalized = clean_text(value, 20)
    ranks = {
        "0": 0.0,
        "1": 1.0,
        "2": 2.0,
        "3": 3.0,
        "4": 4.0,
        "5-": 5.0,
        "5弱": 5.0,
        "5+": 5.5,
        "5強": 5.5,
        "6-": 6.0,
        "6弱": 6.0,
        "6+": 6.5,
        "6強": 6.5,
        "7": 7.0,
    }
    return ranks.get(normalized)


def _normalized_magnitude(value: str, description: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or description).upper().strip()
    match = re.search(r"(?:M)?\s*([0-9]+(?:\.[0-9]+)?)", normalized)
    return f"M{match.group(1)}" if match else clean_text(normalized, 30)


def _first_sentence(value: str, limit: int) -> str:
    sentence = re.split(r"(?<=[。！？!?])", clean_text(value, limit * 2), maxsplit=1)[0]
    return clean_text(sentence, limit)


def _join_official_details(values: list[str], limit: int) -> str:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = clean_text(value, 500)
        key = unicodedata.normalize("NFKC", cleaned).casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        if not cleaned.endswith(("。", "！", "？", "!", "?")):
            cleaned += "。"
        if len(" ".join(result + [cleaned])) > limit:
            break
        result.append(cleaned)
    return " ".join(result)
