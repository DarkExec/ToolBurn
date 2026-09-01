"""Explicit, versioned semantic assignments over factual Toolburn episodes."""

from __future__ import annotations

import json
import re
from pathlib import Path


SEMANTIC_SCHEMA = "toolburn-semantics/v1"
DEFAULT_SEMANTICS_PATH = Path.home() / ".config" / "toolburn" / "semantics.json"
SEMANTIC_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
EPISODE_ID_RE = re.compile(r"[0-9a-f]{24}")


class SemanticCatalogError(RuntimeError):
    """A semantic catalog is malformed or internally inconsistent."""


def load_semantic_catalog(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SemanticCatalogError(f"cannot read semantic catalog {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SemanticCatalogError(f"invalid semantic catalog JSON at {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SEMANTIC_SCHEMA:
        raise SemanticCatalogError(f"semantic catalog schema must be {SEMANTIC_SCHEMA}")
    definitions = payload.get("definitions")
    assignments = payload.get("assignments")
    if not isinstance(definitions, list) or not isinstance(assignments, list):
        raise SemanticCatalogError("semantic catalog requires definitions and assignments arrays")

    definition_keys: set[tuple[str, int]] = set()
    normalized_definitions: list[dict] = []
    for item in definitions:
        if not isinstance(item, dict):
            raise SemanticCatalogError("each semantic definition must be an object")
        semantic_id = str(item.get("id") or "")
        version = item.get("version")
        description = item.get("description")
        if not SEMANTIC_ID_RE.fullmatch(semantic_id):
            raise SemanticCatalogError(f"invalid semantic id: {semantic_id!r}")
        if not isinstance(version, int) or version < 1:
            raise SemanticCatalogError(f"semantic {semantic_id} requires a positive integer version")
        if not isinstance(description, str) or not description.strip():
            raise SemanticCatalogError(f"semantic {semantic_id}@{version} requires a description")
        key = (semantic_id, version)
        if key in definition_keys:
            raise SemanticCatalogError(f"duplicate semantic definition: {semantic_id}@{version}")
        definition_keys.add(key)
        normalized_definitions.append(
            {"id": semantic_id, "version": version, "description": description.strip()}
        )

    assigned_episodes: set[str] = set()
    normalized_assignments: list[dict] = []
    for item in assignments:
        if not isinstance(item, dict):
            raise SemanticCatalogError("each semantic assignment must be an object")
        episode_id = str(item.get("episode_id") or "")
        semantic_id = str(item.get("semantic_id") or "")
        semantic_version = item.get("semantic_version")
        if not EPISODE_ID_RE.fullmatch(episode_id):
            raise SemanticCatalogError(f"invalid episode id: {episode_id!r}")
        if (semantic_id, semantic_version) not in definition_keys:
            raise SemanticCatalogError(
                f"assignment references missing semantic definition: {semantic_id}@{semantic_version}"
            )
        if episode_id in assigned_episodes:
            raise SemanticCatalogError(f"episode has multiple semantic assignments: {episode_id}")
        assigned_episodes.add(episode_id)
        normalized_assignments.append(
            {
                "episode_id": episode_id,
                "semantic_id": semantic_id,
                "semantic_version": semantic_version,
            }
        )
    return {
        "schema": SEMANTIC_SCHEMA,
        "definitions": normalized_definitions,
        "assignments": normalized_assignments,
    }


def semantic_summary(episodes: list[dict], catalog: dict | None) -> dict:
    by_episode = {str(row["episode_id"]): row for row in episodes}
    total_uncached = sum(int(row.get("uncached_tokens") or 0) for row in episodes)
    if catalog is None:
        return {
            "catalog": False,
            "total_episodes": len(episodes),
            "total_uncached_tokens": total_uncached,
            "labeled_episodes": 0,
            "labeled_uncached_tokens": 0,
            "unmatched_assignments": 0,
            "semantics": [],
        }

    aggregates: dict[tuple[str, int], dict] = {}
    labeled_episode_ids: set[str] = set()
    unmatched = 0
    for assignment in catalog["assignments"]:
        episode_id = assignment["episode_id"]
        episode = by_episode.get(episode_id)
        if episode is None:
            unmatched += 1
            continue
        labeled_episode_ids.add(episode_id)
        key = (assignment["semantic_id"], assignment["semantic_version"])
        aggregate = aggregates.setdefault(
            key,
            {
                "label": f"{key[0]}@{key[1]}",
                "events": 0,
                "raw_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "uncached_tokens": 0,
                "episodes": 0,
            },
        )
        aggregate["events"] += int(episode.get("events") or 0)
        aggregate["raw_tokens"] += int(episode.get("raw_tokens") or 0)
        aggregate["cached_input_tokens"] += int(episode.get("cached_input_tokens") or 0)
        aggregate["output_tokens"] += int(episode.get("output_tokens") or 0)
        aggregate["uncached_tokens"] += int(episode.get("uncached_tokens") or 0)
        aggregate["episodes"] += 1
    labeled_uncached = sum(
        int(by_episode[episode_id].get("uncached_tokens") or 0)
        for episode_id in labeled_episode_ids
    )
    return {
        "catalog": True,
        "total_episodes": len(episodes),
        "total_uncached_tokens": total_uncached,
        "labeled_episodes": len(labeled_episode_ids),
        "labeled_uncached_tokens": labeled_uncached,
        "unmatched_assignments": unmatched,
        "semantics": sorted(
            aggregates.values(), key=lambda row: int(row["uncached_tokens"]), reverse=True
        ),
    }


def format_semantic_summary(summary: dict, path: Path) -> str:
    total_episodes = int(summary["total_episodes"])
    labeled_episodes = int(summary["labeled_episodes"])
    total_uncached = int(summary["total_uncached_tokens"])
    labeled_uncached = int(summary["labeled_uncached_tokens"])
    ratio = (labeled_uncached / total_uncached * 100.0) if total_uncached else 0.0
    if not summary["catalog"]:
        return (
            f"no catalog at {path}; 0/{total_episodes} episodes labeled, "
            f"0/{total_uncached} uncached tokens covered"
        )
    lines = [
        f"catalog {path}",
        f"{labeled_episodes}/{total_episodes} episodes labeled; "
        f"{labeled_uncached}/{total_uncached} uncached tokens covered ({ratio:.1f}%)",
    ]
    unmatched = int(summary.get("unmatched_assignments") or 0)
    if unmatched:
        lines.append(f"{unmatched} catalog assignments are outside this report window")
    if summary["semantics"]:
        lines.append("")
        for row in summary["semantics"]:
            lines.append(
                f"{int(row['uncached_tokens']):>10} uncached  "
                f"{int(row['raw_tokens']):>12} raw  {int(row['episodes']):>5} episodes  {row['label']}"
            )
    return "\n".join(lines)
