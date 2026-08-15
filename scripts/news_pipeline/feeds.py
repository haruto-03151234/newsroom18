from __future__ import annotations

import calendar
import gzip
import io
import json
import logging
import socket
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import feedparser

from .models import CATEGORIES, Candidate
from .text_utils import canonical_url, clean_text, stable_hash


LOGGER = logging.getLogger(__name__)
USER_AGENT = "NewsBriefJP/1.0 (+https://github.com/; feed reader)"
MAX_FEED_BYTES = 2_000_000


class _ValidatedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        canonical_url(newurl)
        host = (urlsplit(newurl).hostname or "").lower()
        if not any(host == allowed or host.endswith(f".{allowed}") for allowed in self.allowed_hosts):
            raise urllib.error.HTTPError(newurl, code, "redirect host is not allowlisted", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl.strip())


def load_feed_config(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    feeds = raw.get("feeds", [])
    if not isinstance(feeds, list) or not feeds:
        raise ValueError("feed config must contain a non-empty feeds array")
    for item in feeds:
        if item.get("category") not in CATEGORIES:
            raise ValueError(f"unsupported category: {item.get('category')}")
        canonical_url(str(item["url"]))
    return feeds


def collect_candidates(
    feeds: list[dict[str, Any]],
    start: datetime,
    end: datetime,
    grace_hours: float = 1.5,
    max_workers: int = 8,
) -> tuple[list[Candidate], list[str]]:
    candidates: list[Candidate] = []
    failures: list[str] = []
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
                earliest = start.timestamp() - grace_hours * 3600
                if earliest <= candidate.published_at.timestamp() < end.timestamp():
                    candidates.append(candidate)
    candidates.sort(key=lambda c: (c.priority, c.published_at), reverse=True)
    return candidates, failures


def _fetch_one(config: dict[str, Any]) -> list[Any]:
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
            if length and int(length) > MAX_FEED_BYTES:
                raise RuntimeError("feed exceeds size limit")
            payload = response.read(MAX_FEED_BYTES + 1)
            content_encoding = response.headers.get("Content-Encoding", "").lower()
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"unable to retrieve feed: {exc}") from exc
    if len(payload) > MAX_FEED_BYTES:
        raise RuntimeError("feed exceeds size limit")
    if "gzip" in content_encoding or payload.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
                payload = compressed.read(MAX_FEED_BYTES + 1)
        except OSError as exc:
            raise RuntimeError("feed gzip payload is invalid") from exc
        if len(payload) > MAX_FEED_BYTES:
            raise RuntimeError("decompressed feed exceeds size limit")
    upper = payload[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise RuntimeError("feed contains a disallowed document type")
    parsed = feedparser.parse(payload)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"invalid feed: {parsed.bozo_exception}")
    return list(parsed.entries)


def _entry_to_candidate(entry: Any, config: dict[str, Any]) -> Candidate | None:
    title = clean_text(entry.get("title"), 260)
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
    parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed_time:
        return None
    published = datetime.fromtimestamp(calendar.timegm(parsed_time), tz=UTC)
    description = ""
    if not config.get("metadataOnly"):
        description = clean_text(
            entry.get("summary") or entry.get("description") or entry.get("subtitle"), 900
        )
    identity = stable_hash(f"{config['name']}|{url}|{published.isoformat()}", 18)
    return Candidate(
        id=identity,
        title=title,
        description=description,
        url=url,
        source_name=clean_text(str(config["name"]), 80),
        category=str(config["category"]),
        published_at=published,
        priority=max(1, min(5, int(config.get("priority", 3)))),
        ai_required=bool(config.get("aiRequired", False)),
        primary_source=bool(config.get("primarySource", False)),
    )
