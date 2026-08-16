#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from datetime import datetime
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
from news_pipeline.models import Candidate  # noqa: E402
from news_pipeline.publisher import build_edition, publish_edition  # noqa: E402
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
        start=windows[0].start,
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
        drafts, mode = create_drafts(fresh)
        edition = build_edition(
            window=window,
            drafts=drafts,
            candidates=fresh,
            generated_at=now,
            generation_mode=mode,
            feed_failures=failures,
            site_config=site_config,
        )
        _assert_publishable_source_mix(edition)
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
