#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from news_pipeline.dedupe import (  # noqa: E402
    filter_seen,
    load_state,
    prune_state,
    remember_articles,
)
from news_pipeline.editor import create_drafts  # noqa: E402
from news_pipeline.feeds import collect_candidates, load_feed_config  # noqa: E402
from news_pipeline.models import (  # noqa: E402
    CATEGORIES,
    Candidate,
)
from news_pipeline.publisher import build_edition, publish_edition  # noqa: E402
from news_pipeline.text_utils import (  # noqa: E402
    clean_text,
    has_balanced_brackets,
    has_truncated_sentence,
    stable_hash,
    title_similarity,
)
from news_pipeline.time_windows import (  # noqa: E402
    CoverageWindow,
    JST,
    coverage_window,
    missing_windows,
)


LOGGER = logging.getLogger("news_pipeline")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect and publish a Japanese news digest")
    parser.add_argument("--edition", default="auto", choices=("auto", "06", "12", "18"))
    parser.add_argument("--now", help="Override current time with an ISO-8601 timestamp")
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--force", action="store_true", help="Regenerate the target edition")
    parser.add_argument("--catchup-limit", type=int, default=int(os.getenv("CATCHUP_LIMIT", "9")))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = args.root.resolve()
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(JST)
    if now.tzinfo is None:
        raise ValueError("--now must include a timezone offset")
    now = now.astimezone(JST)

    feeds = load_feed_config(root / "config" / "feeds.json")
    site_config = json.loads((root / "config" / "site.json").read_text(encoding="utf-8"))
    state_path = root / ".state" / "news-state.json"
    state = load_state(state_path)
    prune_state(state, now)
    target = coverage_window(now, args.edition)
    windows = [target] if args.force else missing_windows(
        state.get("lastCompletedEnd"), target, max(1, args.catchup_limit)
    )
    if not windows:
        LOGGER.info("Edition %s is already complete; nothing to publish", target.id)
        _write_summary(target.id, 0, 0, 0, "no-op")
        return 0

    LOGGER.info(
        "Processing %d edition(s), %s through %s",
        len(windows),
        windows[0].id,
        windows[-1].id,
    )
    candidates, failures = collect_candidates(
        feeds,
        # The active edition remains a fixed 6/12-hour window. The earlier
        # fetch range supplies read-only material for explicitly labelled
        # 24-hour continuation desks when a slot has few fresh stories.
        start=windows[0].start - timedelta(hours=24),
        end=windows[-1].end,
        grace_hours=1.5,
    )
    successful_feeds = len(feeds) - len(failures)
    minimum_success = max(1, (len(feeds) + 3) // 4)
    if successful_feeds < minimum_success:
        raise RuntimeError(
            f"Only {successful_feeds}/{len(feeds)} feeds succeeded; state was not advanced"
        )

    published_count = 0
    generation_modes: list[str] = []
    prepared_editions: list[tuple[CoverageWindow, dict[str, Any], str]] = []
    for window in windows:
        earliest = window.start.timestamp() - 1.5 * 3600
        in_window = [
            candidate
            for candidate in candidates
            if earliest <= candidate.published_at.timestamp() < window.end.timestamp()
        ]
        fresh = _select_fresh_candidates(in_window, state, window.id, force=args.force)
        context = _rolling_context_candidates(root, candidates, window, fresh)
        drafts, mode = create_drafts(fresh, context_candidates=context)
        evidence = _merge_candidate_evidence(fresh, context)
        edition = build_edition(
            window=window,
            drafts=drafts,
            candidates=evidence,
            generated_at=now,
            generation_mode=mode,
            feed_failures=failures,
            site_config=site_config,
        )
        _assert_publishable_source_mix(edition)
        _assert_publishable_feature_floor(edition)
        # Remember prepared editions in memory so a later catch-up window does
        # not republish the same event. Public files and persistent state are
        # untouched until every pending window passes the quality gate.
        remember_articles(state, edition["articles"], window.id)
        _record_completion(state, window)
        prepared_editions.append((window, edition, mode))
        published_count += len(edition["articles"])
        generation_modes.append(mode)

    for window, edition, mode in prepared_editions:
        publish_edition(root, edition, root / "templates" / "article.md.tmpl")
        LOGGER.info(
            "Published %s: %d articles (%s)", window.id, len(edition["articles"]), mode
        )

    state["updatedAt"] = now.isoformat()
    _write_json_atomic(state_path, state)
    _write_summary(
        windows[-1].id,
        len(candidates),
        published_count,
        len(failures),
        ", ".join(sorted(set(generation_modes))),
    )
    return 0


def _assert_publishable_source_mix(edition: dict[str, Any]) -> None:
    """Reject an empty or single-newsroom non-primary edition before writes."""
    raw_articles = edition.get("articles", [])
    articles = raw_articles if isinstance(raw_articles, list) else []
    sources: list[dict[str, Any]] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        raw_sources = article.get("sources", [])
        if isinstance(raw_sources, list):
            sources.extend(
                source for source in raw_sources if isinstance(source, dict)
            )
    publishers = {
        str(source.get("publisherId") or source.get("name") or "").strip()
        for source in sources
        if source.get("publisherId") or source.get("name")
    }
    has_primary = any(source.get("isPrimary") is True for source in sources)
    edition_meta = edition.get("edition", {})
    edition_id = (
        edition_meta.get("id", "unknown")
        if isinstance(edition_meta, dict)
        else "unknown"
    )
    if not articles or not publishers:
        raise RuntimeError(
            f"Edition {edition_id} has no publishable sourced articles; "
            "publication and state advancement were aborted"
        )
    if len(publishers) == 1 and not has_primary:
        raise RuntimeError(
            f"Edition {edition_id} contains only one non-primary publisher; "
            "publication and state advancement were aborted"
        )


def _assert_publishable_feature_floor(edition: dict[str, Any]) -> None:
    """Reject any feature that is not one substantial, coherent event."""
    raw_articles = edition.get("articles", [])
    articles = raw_articles if isinstance(raw_articles, list) else []
    features = [
        article
        for article in articles
        if isinstance(article, dict) and article.get("articleType") == "feature"
    ]
    qualified = [
        article
        for article in features
        if _is_qualified_desk_feature(article)
    ]
    edition_meta = edition.get("edition", {})
    edition_id = (
        edition_meta.get("id", "unknown")
        if isinstance(edition_meta, dict)
        else "unknown"
    )
    if len(qualified) != len(features):
        raise RuntimeError(
            f"Edition {edition_id} contains {len(features) - len(qualified)} "
            "unqualified or mixed-event feature(s); publication and state "
            "advancement were aborted"
        )
    if not qualified:
        return
    titles = [str(article.get("title", "")) for article in qualified]
    if len(set(titles)) != len(titles):
        raise RuntimeError(
            f"Edition {edition_id} repeats a feature title; publication "
            "and state advancement were aborted"
        )
    event_sets = [set(_feature_values(article.get("eventKeys"))) for article in qualified]
    if any(
        event_sets[left] & event_sets[right]
        for left in range(len(event_sets))
        for right in range(left + 1, len(event_sets))
    ):
        raise RuntimeError(
            f"Edition {edition_id} reuses an event across desk features; "
            "publication and state advancement were aborted"
        )


def _is_qualified_desk_feature(article: dict[str, Any]) -> bool:
    if article.get("articleType") != "feature":
        return False
    try:
        source_count = int(article.get("sourceCount", 0))
        fresh_source_count = int(article.get("freshSourceCount", 0))
        publisher_count = int(article.get("publisherCount", 0))
    except (TypeError, ValueError):
        return False
    tags = article.get("tags", [])
    categories = (
        {str(tag) for tag in tags if isinstance(tag, str) and tag in CATEGORIES}
        if isinstance(tags, list)
        else set()
    )
    lens = str(article.get("deskLens", ""))
    event_keys = set(_feature_values(article.get("eventKeys")))
    facts = _feature_values(article.get("facts"))
    impact = _feature_values(article.get("impactPoints"))
    watch = _feature_values(article.get("watchPoints"))
    background = str(article.get("background", "")).strip()
    grounded_values = facts + impact + ([background] if background else []) + watch
    grounded_chars = len(re.sub(r"\s+", "", " ".join(grounded_values)))
    point_count = len(facts) + len(impact) + len(watch)
    if background:
        point_count += max(1, len(re.findall(r"[。！？!?]", background)))
    dek = str(article.get("dek", ""))
    explicit_scope = bool(
        re.search(
            r"\d{1,2}月\d{1,2}日(?:\s+\d{2}:\d{2}|\d{1,2}時\d{2}分)",
            dek,
        )
    )
    raw_sources = article.get("sources", [])
    sources = raw_sources if isinstance(raw_sources, list) else []
    has_primary = any(
        isinstance(source, dict) and source.get("isPrimary") is True
        for source in sources
    )
    has_structured_jma = any(
        isinstance(source, dict)
        and source.get("isPrimary") is True
        and str(source.get("publisherId", "")).casefold() == "jma"
        for source in sources
    ) and sum(
        bool(pattern.search(" ".join(grounded_values)))
        for pattern in (
            re.compile(r"発生時刻(?:は|\s*[:：])"),
            re.compile(r"(?:震源は|震央・震源地域\s*[:：])"),
            re.compile(r"(?:地震の規模は)?マグニチュード"),
            re.compile(r"最大震度(?:は|\s*[:：])"),
            re.compile(r"津波.*(?:心配|影響).*(?:ありません|ない)"),
        )
    ) >= 3
    copy_values = [
        str(article.get("title", "")),
        dek,
        str(article.get("summary", "")),
        *grounded_values,
    ]
    return (
        lens == "event"
        and source_count >= (1 if has_primary else 2)
        and fresh_source_count >= 1
        and (has_primary or publisher_count >= 2)
        and bool(categories)
        and len(event_keys) == 1
        and point_count >= 3
        and (grounded_chars >= 180 or has_structured_jma)
        and len(re.sub(r"\s+", "", str(article.get("summary", "")))) >= 80
        and explicit_scope
        and _article_has_source_links(article, minimum=1 if has_primary else 2)
        and _feature_title_is_complete(str(article.get("title", "")))
        and _feature_copy_is_complete(copy_values)
    )


def _feature_title_is_complete(title: str) -> bool:
    value = clean_text(title, 500)
    if not (8 <= len(value) <= 120):
        return False
    if re.search(r"(?:\.\.\.|[/／｜|：:、,（(【「『-])$", value):
        return False
    return has_balanced_brackets(value) and not has_truncated_sentence(value)


def _feature_copy_is_complete(values: list[str]) -> bool:
    forbidden = re.compile(
        r"(?:と配信|を軸に|主要\d+項目|面の焦点|横断整理|配信元の|"
        r"NEWSROOM 18が要約|公開情報を確認)"
    )
    return all(
        has_balanced_brackets(value)
        and not has_truncated_sentence(value)
        and not forbidden.search(value)
        for value in values
        if value
    )


def _feature_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _article_has_source_links(article: dict[str, Any], minimum: int) -> bool:
    urls: set[str] = set()
    sources = article.get("sources", [])
    if not isinstance(sources, list):
        return False
    for source in sources:
        if not isinstance(source, dict):
            continue
        if source.get("url"):
            urls.add(str(source["url"]))
        links = source.get("links", [])
        if not isinstance(links, list):
            continue
        for link in links:
            if isinstance(link, dict) and link.get("url"):
                urls.add(str(link["url"]))
    return len(urls) >= minimum


def _select_fresh_candidates(
    candidates: list[Candidate],
    state: dict[str, object],
    edition_id: str,
    force: bool,
) -> list[Candidate]:
    if force:
        stories = state.get("stories", [])
        if not isinstance(stories, list):
            stories = []
        # A forced edition replaces its own remembered stories. Duplicates
        # from other editions still protect against republishing old news.
        state["stories"] = [
            story
            for story in stories
            if not isinstance(story, dict) or story.get("editionId") != edition_id
        ]
    return filter_seen(candidates, state)


def _rolling_context_candidates(
    root: Path,
    collected: list[Candidate],
    window: CoverageWindow,
    fresh: list[Candidate],
) -> list[Candidate]:
    """Return read-only 24-hour context with explicit origin metadata."""
    cutoff = window.end - timedelta(hours=24)
    fresh_urls = {item.url for item in fresh}
    context: list[Candidate] = []
    for candidate in collected:
        published = candidate.published_at.astimezone(JST)
        if not (cutoff <= published < window.start):
            continue
        if candidate.url in fresh_urls:
            continue
        context.append(
            replace(
                candidate,
                id=f"context-{stable_hash(candidate.url, 20)}",
                context_only=True,
                origin_edition_id=_edition_id_for_timestamp(published),
            )
        )
    context.extend(_archive_context_candidates(root, cutoff, window.start))
    return _merge_candidate_evidence([], context)[:24]


def _archive_context_candidates(
    root: Path, cutoff: datetime, before: datetime
) -> list[Candidate]:
    """Reconstruct safe source records from previously published editions."""
    editions_dir = root / "site" / "data" / "editions"
    if not editions_dir.exists():
        return []
    result: list[Candidate] = []
    for path in sorted(editions_dir.glob("*.json"), reverse=True)[:12]:
        try:
            edition = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        edition_meta = edition.get("edition", {})
        origin_id = (
            str(edition_meta.get("id", path.stem))
            if isinstance(edition_meta, dict)
            else path.stem
        )
        articles = edition.get("articles", [])
        if not isinstance(articles, list):
            continue
        for article in articles:
            if not isinstance(article, dict):
                continue
            # Never build a new continuation from an older desk roundup. Use
            # its underlying individual articles instead, preventing recursive
            # amplification of editorial framing.
            try:
                if int(article.get("sourceCount", 0)) >= 3:
                    continue
            except (TypeError, ValueError):
                continue
            sources = article.get("sources", [])
            if not isinstance(sources, list):
                continue
            single_source = len(sources) == 1
            for source in sources:
                if not isinstance(source, dict):
                    continue
                links = source.get("links", [])
                if not isinstance(links, list) or not links:
                    links = [
                        {
                            "title": article.get("title", ""),
                            "url": source.get("url", ""),
                            "publishedAt": source.get(
                                "publishedAt", article.get("publishedAt", "")
                            ),
                        }
                    ]
                for link in links:
                    if not isinstance(link, dict) or not link.get("url"):
                        continue
                    raw_published = link.get(
                        "publishedAt",
                        source.get("publishedAt", article.get("publishedAt", "")),
                    )
                    try:
                        published = datetime.fromisoformat(str(raw_published)).astimezone(JST)
                    except (TypeError, ValueError):
                        continue
                    if not (cutoff <= published < before):
                        continue
                    title = clean_text(
                        str(link.get("title") or article.get("title", "")), 180
                    )
                    if not title:
                        continue
                    description = _archive_source_description(
                        article, source, single_source
                    )
                    url = str(link["url"])
                    try:
                        priority = max(
                            1, min(5, int(article.get("importance", 3)))
                        )
                    except (TypeError, ValueError):
                        priority = 3
                    result.append(
                        Candidate(
                            id=f"context-{stable_hash(origin_id + '|' + url, 20)}",
                            title=title,
                            description=description,
                            url=url,
                            source_name=clean_text(
                                str(source.get("name", "既報の配信元")), 80
                            ),
                            publisher_id=clean_text(
                                str(
                                    source.get("publisherId")
                                    or source.get("name")
                                    or "archive"
                                ),
                                60,
                            ),
                            category=(
                                str(article.get("category"))
                                if article.get("category") in CATEGORIES
                                else "その他"
                            ),
                            published_at=published,
                            priority=priority,
                            primary_source=source.get("isPrimary") is True,
                            context_only=True,
                            origin_edition_id=origin_id,
                        )
                    )
    return result


def _archive_source_description(
    article: dict[str, Any], source: dict[str, Any], single_source: bool
) -> str:
    values: list[str] = []
    article_title = str(article.get("title", ""))
    key_points = source.get("keyPoints", [])
    if isinstance(key_points, list):
        values.extend(
            str(value)
            for value in key_points
            if "NEWSROOM 18が要約・加工" not in str(value)
            and "公開情報をもとに" not in str(value)
            and title_similarity(str(value), article_title) < 0.86
        )
    if single_source:
        for field in ("facts", "impactPoints", "watchPoints"):
            raw = article.get(field, [])
            if isinstance(raw, list):
                values.extend(str(value) for value in raw)
        if article.get("background"):
            values.append(str(article["background"]))
    return clean_text(" ".join(dict.fromkeys(values)), 900)


def _edition_id_for_timestamp(published: datetime) -> str:
    local = published.astimezone(JST)
    if 6 <= local.hour < 12:
        end = local.replace(hour=12, minute=0, second=0, microsecond=0)
        slot = "12"
    elif 12 <= local.hour < 18:
        end = local.replace(hour=18, minute=0, second=0, microsecond=0)
        slot = "18"
    else:
        end = local.replace(hour=6, minute=0, second=0, microsecond=0)
        if local.hour >= 18:
            end += timedelta(days=1)
        slot = "06"
    return f"{end:%Y-%m-%d}-{slot}"


def _merge_candidate_evidence(
    fresh: list[Candidate], context: list[Candidate]
) -> list[Candidate]:
    by_url: dict[str, Candidate] = {}
    for candidate in fresh + context:
        existing = by_url.get(candidate.url)
        if existing is None:
            by_url[candidate.url] = candidate
            continue
        if existing.context_only and not candidate.context_only:
            by_url[candidate.url] = candidate
            continue
        if not existing.context_only and candidate.context_only:
            continue
        if len(candidate.description) > len(existing.description):
            by_url[candidate.url] = candidate
    return list(by_url.values())


def _record_completion(state: dict[str, object], window: CoverageWindow) -> None:
    current: datetime | None = None
    raw_current = state.get("lastCompletedEnd")
    if raw_current:
        try:
            parsed = datetime.fromisoformat(str(raw_current))
            if parsed.tzinfo is not None:
                current = parsed.astimezone(JST)
        except ValueError:
            current = None
    if current is not None and current > window.end:
        return
    state["lastCompletedEnd"] = window.end.isoformat()
    state["lastCompletedEdition"] = window.id


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_summary(
    edition_id: str,
    candidate_count: int,
    published_count: int,
    failure_count: int,
    mode: str,
) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY", "")
    if not summary_path:
        return
    lines = [
        "## ニュース更新結果",
        "",
        f"- 対象版: `{edition_id}`",
        f"- 候補件数: {candidate_count}",
        f"- 掲載件数: {published_count}",
        f"- 取得失敗フィード: {failure_count}",
        f"- 編集方式: `{mode}`",
        "",
    ]
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
