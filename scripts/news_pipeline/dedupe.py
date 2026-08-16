from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .models import Candidate
from .text_utils import normalize_title, title_similarity


STATE_VERSION = 1


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": STATE_VERSION, "lastCompletedEnd": None, "stories": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "lastCompletedEnd": None, "stories": []}
    if state.get("version") != STATE_VERSION or not isinstance(state.get("stories"), list):
        return {"version": STATE_VERSION, "lastCompletedEnd": None, "stories": []}
    return state


def prune_state(state: dict[str, Any], now: datetime, retention_days: int = 180) -> None:
    cutoff = now - timedelta(days=retention_days)
    kept: list[dict[str, Any]] = []
    for story in state.get("stories", []):
        try:
            published = datetime.fromisoformat(str(story["publishedAt"]))
        except (KeyError, TypeError, ValueError):
            continue
        if published.tzinfo and published >= cutoff:
            kept.append(story)
    state["stories"] = kept[-8000:]


def filter_seen(candidates: list[Candidate], state: dict[str, Any]) -> list[Candidate]:
    stories = state.get("stories", [])
    seen_urls = {url for story in stories for url in story.get("urls", [])}
    accepted: list[Candidate] = []
    for candidate in candidates:
        if candidate.url in seen_urls:
            continue
        # Official bulletins often reuse a generic title for a changed warning,
        # cancellation or later observation while issuing a new URL. Do not
        # discard that update solely because its title resembles a prior item.
        # Exact URL repeats remain suppressed above; ordinary reporting keeps
        # the existing 36-hour title-similarity protection below.
        if candidate.primary_source:
            accepted.append(candidate)
            continue
        duplicate = False
        for story in reversed(stories[-350:]):
            try:
                previous_time = datetime.fromisoformat(str(story["publishedAt"]))
            except (KeyError, TypeError, ValueError):
                continue
            if not previous_time.tzinfo:
                continue
            if abs(candidate.published_at - previous_time) > timedelta(hours=36):
                continue
            previous_title = str(story.get("title", ""))
            if title_similarity(candidate.title, previous_title) >= 0.86:
                duplicate = True
                break
        if not duplicate:
            accepted.append(candidate)
    return accepted


def remember_articles(
    state: dict[str, Any], articles: list[dict[str, Any]], edition_id: str
) -> None:
    for article in articles:
        sources = article.get("sources", [])
        if not sources:
            continue
        urls: set[str] = set()
        for source in sources:
            if not isinstance(source, dict):
                continue
            if source.get("url"):
                urls.add(str(source["url"]))
            links = source.get("links", [])
            if not isinstance(links, list):
                continue
            urls.update(
                str(link["url"])
                for link in links
                if isinstance(link, dict) and link.get("url")
            )
        if not urls:
            continue
        state.setdefault("stories", []).append(
            {
                "editionId": edition_id,
                "title": normalize_title(str(article.get("title", ""))),
                "publishedAt": str(article.get("publishedAt")),
                "urls": sorted(urls),
            }
        )
