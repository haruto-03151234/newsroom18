from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


CATEGORIES = ("国内", "海外", "テクノロジー", "エンタメ", "スポーツ", "その他")
ARTICLE_TYPES = ("brief", "feature")


@dataclass(frozen=True)
class Candidate:
    id: str
    title: str
    description: str
    url: str
    source_name: str
    category: str
    published_at: datetime
    priority: int = 3
    ai_required: bool = False
    primary_source: bool = False
    publisher_id: str = ""

    def prompt_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "source": self.source_name,
            "publisher": self.publisher_id or self.source_name,
            "category": self.category,
            "publishedAt": self.published_at.isoformat(),
            "priority": self.priority,
            "primarySource": self.primary_source,
        }


@dataclass
class StoryDraft:
    candidate_ids: list[str]
    title: str
    dek: str
    summary: str
    why_it_matters: str
    category: str
    importance: int
    tags: list[str]
    facts: list[str] = field(default_factory=list)
    impact: list[str] = field(default_factory=list)
    background: str = ""
    watch_points: list[str] = field(default_factory=list)
    source_notes: dict[str, str] = field(default_factory=dict)
    article_type: str = "brief"


def dataclass_dict(value: Any) -> dict[str, Any]:
    return asdict(value)
