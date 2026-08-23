#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path
from urllib.parse import urlparse

from markdown_it import MarkdownIt


README_PATH = Path(__file__).resolve().parents[2] / "README.md"
GITHUB_BLOB_BASE = "https://github.com/DarkExec/ToolBurn/blob/main/"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def token_plain_text(token) -> str:
    if not getattr(token, "children", None):
        return token.content or ""
    return "".join(child.content or "" for child in token.children)


def rewrite_href(href: str) -> str:
    if not href or href.startswith("#"):
        return href
    parsed = urlparse(href)
    if parsed.scheme or parsed.netloc:
        return href
    return GITHUB_BLOB_BASE + href.lstrip("/")


def render_markdown(markdown: str) -> str:
    md = MarkdownIt("default", {"html": False, "linkify": False, "typographer": True})
    tokens = md.parse(markdown)
    used_ids: dict[str, int] = {}

    for index, token in enumerate(tokens):
        if token.type == "heading_open" and index + 1 < len(tokens):
            base = slugify(token_plain_text(tokens[index + 1]))
            count = used_ids.get(base, 0)
            used_ids[base] = count + 1
            token.attrSet("id", base if count == 0 else f"{base}-{count + 1}")
        if token.type == "link_open":
            href = token.attrGet("href") or ""
            token.attrSet("href", rewrite_href(href))
            if href.startswith("http://") or href.startswith("https://"):
                token.attrSet("rel", "noopener")

    return md.renderer.render(tokens, md.options, {})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--readme", default=str(README_PATH))
    args = parser.parse_args()

    readme_path = Path(args.readme)
    try:
        markdown = readme_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"<p>Could not read README: {html.escape(str(exc))}</p>")
        return 1

    print(render_markdown(markdown))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
