from __future__ import annotations

import json
import os
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .models import Candidate, StoryDraft
from .text_utils import clean_text, stable_hash
from .time_windows import CoverageWindow, JST


def build_edition(
    window: CoverageWindow,
    drafts: list[StoryDraft],
    candidates: list[Candidate],
    generated_at: datetime,
    generation_mode: str,
    feed_failures: list[str],
    site_config: dict[str, Any],
) -> dict[str, Any]:
    by_id = {candidate.id: candidate for candidate in candidates}
    articles: list[dict[str, Any]] = []
    for draft in drafts:
        evidence = [by_id[value] for value in draft.candidate_ids if value in by_id]
        if not evidence:
            continue
        sources: list[dict[str, str]] = []
        source_urls: set[str] = set()
        for item in sorted(evidence, key=lambda value: value.published_at):
            if item.url in source_urls:
                continue
            source_urls.add(item.url)
            sources.append(
                {
                    "name": item.source_name,
                    "url": item.url,
                    "publishedAt": item.published_at.astimezone(JST).isoformat(),
                }
            )
        identity = stable_hash("|".join(sorted(source_urls)), 16)
        slug = f"{window.id}-{identity[:8]}"
        published_at = min(item.published_at for item in evidence).astimezone(JST)
        importance = max(1, min(5, int(draft.importance)))
        if importance >= 4 and len({item.source_name for item in evidence}) < 2 and not any(
            item.primary_source for item in evidence
        ):
            importance = 3
        summary = clean_text(draft.summary, 300)
        why_it_matters = clean_text(draft.why_it_matters, 140)
        articles.append(
            {
                "id": identity,
                "slug": slug,
                "publishedAt": published_at.isoformat(),
                "updatedAt": generated_at.astimezone(JST).isoformat(),
                "title": clean_text(draft.title, 70),
                "dek": clean_text(draft.dek, 120),
                "summary": summary,
                "whyItMatters": why_it_matters,
                "body": [value for value in (summary, why_it_matters) if value],
                "sections": [
                    {"heading": "要点", "paragraphs": [summary]},
                    {"heading": "なぜ重要か", "paragraphs": [why_it_matters]},
                ],
                "category": draft.category,
                "importance": importance,
                "sources": sources,
                "tags": [clean_text(tag, 30) for tag in draft.tags[:3]],
            }
        )
    articles.sort(key=lambda item: (item["importance"], item["publishedAt"]), reverse=True)
    if articles and not any(item["importance"] >= 4 for item in articles):
        # Keep one clearly identified lead story even in conservative fallback mode.
        articles[0]["importance"] = 4
    categories = Counter(item["category"] for item in articles)
    return {
        "schemaVersion": 1,
        "site": {
            "name": str(site_config.get("name", "NEWSROOM 18")),
            "tagline": str(site_config.get("tagline", "昨日と今日を、短く、確かに。")),
            "baseUrl": str(site_config.get("baseUrl", "")),
        },
        "generatedAt": generated_at.astimezone(JST).isoformat(),
        "summary": _edition_summary(articles),
        "generationMode": generation_mode,
        "edition": {
            "id": window.id,
            "date": f"{window.end:%Y-%m-%d}",
            "time": f"{window.end:%H:%M}",
            "slot": f"{window.end:%H:%M}",
            "label": _edition_label(window.edition),
        },
        "coverage": {
            "from": window.start.isoformat(),
            "to": window.end.isoformat(),
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "timezone": "Asia/Tokyo",
            "label": f"{window.start:%m月%d日 %H:%M}〜{window.end:%m月%d日 %H:%M}",
        },
        "stats": {
            "articleCount": len(articles),
            "categoryCounts": dict(categories),
            "feedFailureCount": len(feed_failures),
        },
        "feedFailures": feed_failures,
        "articles": articles,
    }


def publish_edition(
    root: Path,
    edition: dict[str, Any],
    article_template: Path,
    archive_limit: int = 540,
) -> None:
    site_data = root / "site" / "data"
    editions_dir = site_data / "editions"
    content_dir = root / "content"
    editions_dir.mkdir(parents=True, exist_ok=True)
    content_dir.mkdir(parents=True, exist_ok=True)
    edition_id = str(edition["edition"]["id"])
    _write_json(editions_dir / f"{edition_id}.json", edition)
    _write_json(site_data / "latest.json", edition)

    archive_path = site_data / "archive.json"
    archive = _read_json(archive_path, {"schemaVersion": 1, "editions": []})
    items = [
        item
        for item in archive.get("editions", [])
        if item.get("id") != edition_id
        and not item.get("isSample")
        and "サンプル" not in str(item.get("label", ""))
    ]
    category_counts = Counter(article["category"] for article in edition["articles"])
    headline = edition["articles"][0]["title"] if edition["articles"] else "この時間帯の新着記事はありません"
    items.insert(
        0,
        {
            "id": edition_id,
            "path": f"data/editions/{edition_id}.json",
            "dataUrl": f"data/editions/{edition_id}.json",
            "generatedAt": edition["generatedAt"],
            "date": edition["edition"]["date"],
            "time": edition["edition"]["time"],
            "label": edition["edition"]["label"],
            "coverageLabel": edition["coverage"]["label"],
            "headline": headline,
            "articleCount": len(edition["articles"]),
            "categories": dict(category_counts),
        },
    )
    archive = {"schemaVersion": 1, "updatedAt": edition["generatedAt"], "editions": items[:archive_limit]}
    _write_json(archive_path, archive)

    template = article_template.read_text(encoding="utf-8")
    markdown = template.format(
        title=f"{edition['edition']['date']} {edition['edition']['label']}ニュースまとめ",
        generated_at=edition["generatedAt"],
        coverage=edition["coverage"]["label"],
        lead=_markdown_lead(edition),
        sections=_markdown_sections(edition),
        generation_mode=edition["generationMode"],
    )
    _atomic_write(content_dir / f"{edition_id}.md", markdown)
    _write_feed(root, archive)
    _write_sitemap(root, archive, edition["site"].get("baseUrl", ""))


def _markdown_lead(edition: dict[str, Any]) -> str:
    count = len(edition["articles"])
    if not count:
        return "この時間帯には、公開条件を満たす新着記事がありませんでした。"
    important = [item for item in edition["articles"] if item["importance"] >= 4]
    if important:
        return f"{count}件を掲載。特に注目度の高いニュースは{len(important)}件です。"
    return f"この時間帯の主なニュースを{count}件に整理しました。"


def _edition_summary(articles: list[dict[str, Any]]) -> str:
    if not articles:
        return "この時間帯には、公開条件を満たす新着記事がありませんでした。"
    important = sum(1 for item in articles if item["importance"] >= 4)
    if important:
        return f"対象時間帯のニュースを{len(articles)}本に整理しました。うち重要ニュースは{important}本です。"
    return f"対象時間帯の主なニュースを{len(articles)}本に整理しました。"


def _markdown_sections(edition: dict[str, Any]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for article in edition["articles"]:
        grouped[article["category"]].append(article)
    sections: list[str] = []
    for category in ("国内", "海外", "テクノロジー", "エンタメ", "スポーツ", "その他"):
        if not grouped[category]:
            continue
        sections.append(f"## {category}")
        for article in grouped[category]:
            sections.append(f"### {_escape_markdown(article['title'])}")
            if article["dek"]:
                sections.append(_escape_markdown(article["dek"]))
            sections.append(_escape_markdown(article["summary"]))
            if article["whyItMatters"]:
                sections.append(f"**注目点:** {_escape_markdown(article['whyItMatters'])}")
            source_lines = []
            for source in article["sources"]:
                source_lines.append(
                    f"- [{_escape_markdown(source['name'])}]({source['url']}) — {source['publishedAt']}"
                )
            sections.append("**出典**\n\n" + "\n".join(source_lines))
    return "\n\n".join(sections) if sections else "掲載記事はありません。"


def _write_feed(root: Path, archive: dict[str, Any]) -> None:
    latest = _read_json(root / "site" / "data" / "latest.json", {})
    site = latest.get("site", {})
    base_url = str(site.get("baseUrl", "")).rstrip("/") + "/"
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = str(site.get("name", "NEWSROOM 18"))
    ET.SubElement(channel, "link").text = base_url
    ET.SubElement(channel, "description").text = str(site.get("tagline", "ニュースダイジェスト"))
    ET.SubElement(channel, "language").text = "ja"
    for meta in archive.get("editions", [])[:20]:
        path = root / "site" / str(meta.get("path", ""))
        data = _read_json(path, {})
        for article in data.get("articles", [])[:5]:
            item = ET.SubElement(channel, "item")
            ET.SubElement(item, "title").text = str(article["title"])
            url = f"{base_url}?article={quote(str(article['slug']))}" if base_url else ""
            ET.SubElement(item, "link").text = url
            ET.SubElement(item, "guid", isPermaLink="false").text = str(article["id"])
            ET.SubElement(item, "description").text = str(article["summary"])
            ET.SubElement(item, "pubDate").text = _rfc2822(str(article["publishedAt"]))
    ET.indent(rss, space="  ")
    xml = ET.tostring(rss, encoding="unicode", xml_declaration=True)
    _atomic_write(root / "site" / "feed.xml", xml + "\n")


def _write_sitemap(root: Path, archive: dict[str, Any], base_url: str) -> None:
    base = str(base_url).rstrip("/")
    if not base:
        _atomic_write(root / "site" / "sitemap.xml", "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\"/>\n")
        return
    namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"
    ET.register_namespace("", namespace)
    urlset = ET.Element(f"{{{namespace}}}urlset")
    root_url = ET.SubElement(urlset, f"{{{namespace}}}url")
    ET.SubElement(root_url, f"{{{namespace}}}loc").text = base + "/"
    ET.SubElement(root_url, f"{{{namespace}}}lastmod").text = str(archive.get("updatedAt", ""))
    xml = ET.tostring(urlset, encoding="unicode", xml_declaration=True)
    _atomic_write(root / "site" / "sitemap.xml", xml + "\n")


def _rfc2822(value: str) -> str:
    from email.utils import format_datetime

    return format_datetime(datetime.fromisoformat(value))


def _edition_label(edition: str) -> str:
    return {"06": "朝6時版", "12": "昼12時版", "18": "夕方6時版"}[edition]


def _escape_markdown(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: Any) -> None:
    _atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
