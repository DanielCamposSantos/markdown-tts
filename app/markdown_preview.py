from __future__ import annotations

from markdown_it import MarkdownIt
from urllib.parse import urlsplit


MAX_MARKDOWN_INPUT_BYTES = 5 * 1024 * 1024


def _link_open(tokens, index, options, env) -> str:
    token = tokens[index]
    token.attrSet("target", "_blank")
    token.attrSet("rel", "noopener noreferrer")
    return renderer.renderer.renderToken(tokens, index, options, env)


renderer = MarkdownIt("commonmark", {"html": False, "linkify": False})
renderer.disable("image")
renderer.renderer.rules["link_open"] = _link_open


def _safe_link(url: str) -> bool:
    try:
        scheme = urlsplit(url.strip()).scheme.lower()
    except ValueError:
        return False
    return not scheme or scheme in {"http", "https", "mailto"}


renderer.validateLink = _safe_link


def render_markdown_preview(markdown: str) -> str:
    return renderer.render(markdown)
