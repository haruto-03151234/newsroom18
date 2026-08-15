from __future__ import annotations

import hashlib
import html
import ipaddress
import re
import unicodedata
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_text(value: str | None, limit: int = 1200) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(value or "")
        raw = " ".join(parser.parts)
    except Exception:
        raw = value or ""
    raw = html.unescape(raw)
    raw = "".join(ch for ch in raw if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:limit].rstrip()


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", clean_text(value, 300)).lower()
    value = re.sub(r"\s*[|｜:：\-—–]\s*[^|｜:：\-—–]{1,28}$", "", value)
    value = re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", "", value)
    return value


def title_similarity(left: str, right: str) -> float:
    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return 0.0
    sequence = SequenceMatcher(None, a, b).ratio()
    grams_a = {a[i : i + 2] for i in range(max(1, len(a) - 1))}
    grams_b = {b[i : i + 2] for i in range(max(1, len(b) - 1))}
    union = grams_a | grams_b
    jaccard = len(grams_a & grams_b) / len(union) if union else 0.0
    containment = min(len(a), len(b)) / max(len(a), len(b)) if a in b or b in a else 0.0
    return max(sequence, jaccard, containment)


def canonical_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
    except Exception as exc:
        raise ValueError("invalid URL") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("only absolute HTTP(S) URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not allowed")

    host = parsed.hostname.lower().rstrip(".")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("local or reserved hosts are not allowed")
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise

    port = parsed.port
    netloc = host
    if port and not ((parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ]
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc, path, urlencode(query), ""))


def stable_hash(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def looks_japanese(value: str) -> bool:
    if not value:
        return False
    japanese = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff]", value))
    letters = len(re.findall(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]", value))
    return letters > 0 and japanese / letters >= 0.12

