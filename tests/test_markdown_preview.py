from html.parser import HTMLParser

import pytest
from fastapi import HTTPException

from app.markdown_parser import parse_markdown
from app.markdown_preview import MAX_MARKDOWN_INPUT_BYTES, render_markdown_preview
from app.speech_plan import build_speech_plan
from web import PreviewRequest, preview_markdown


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attributes = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend((tag, name, value) for name, value in attrs)


def test_preview_renders_commonmark_and_utf8_safely():
    markdown = """# Título

**forte** e *ênfase* com `TLS`.

- item
  - aninhado

1. primeiro
2. segundo

> citação

```python
print("olá")
```

---

[seguro](https://example.com/a?q=1)
"""
    html = render_markdown_preview(markdown)
    for fragment in ("<h1>Título</h1>", "<strong>forte</strong>", "<em>ênfase</em>",
                     "<ul>", "<ol>", "<blockquote>", "<pre><code", "<hr", "olá"):
        assert fragment in html
    assert 'href="https://example.com/a?q=1"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html


@pytest.mark.parametrize("level", range(1, 7))
def test_preview_supports_all_heading_levels(level):
    assert f"<h{level}>Título</h{level}>" in render_markdown_preview(f"{'#' * level} Título")


def test_preview_preserves_soft_breaks_and_technical_symbols():
    html = render_markdown_preview("Linha HTTPS/TLS & TCP\ncontinuação çãõ")
    assert "HTTPS/TLS &amp; TCP\ncontinuação çãõ" in html


@pytest.mark.parametrize("payload, forbidden", [
    ("<script>alert(1)</script>", "<script"),
    ('<img src=x onerror=alert(1)>', "<img"),
    ('<iframe src="https://example.com"></iframe>', "<iframe"),
    ("[click](javascript:alert(1))", 'href="javascript:'),
    ("[arquivo](file:///etc/passwd)", 'href="file:'),
    ("[ftp](ftp://example.com/file)", 'href="ftp:'),
    ("[data](data:text/html,<script>alert(1)</script>)", 'href="data:'),
    ("[encoded](jav&#x61;script:alert(1))", 'href="javascript:'),
])
def test_preview_never_emits_dangerous_html(payload, forbidden):
    html = render_markdown_preview(payload)
    assert forbidden not in html.lower()
    assert "onerror=" not in html.lower() or "&lt;img" in html.lower()


def test_raw_html_is_displayed_as_escaped_text():
    html = render_markdown_preview('<object data="x"><iframe src="x"></iframe></object>')
    assert "&lt;object" in html and "&lt;iframe" in html
    assert "<object" not in html and "<iframe" not in html


def test_preview_output_uses_controlled_tags_and_attributes():
    parser = Tags()
    parser.feed(render_markdown_preview("# H\n\n[link](mailto:a@example.com)\n\n![x](https://x/img.png)"))
    assert set(parser.tags) <= {"h1", "p", "a"}
    assert all(name in {"href", "target", "rel", "title"} for _, name, _ in parser.attributes)


def test_preview_api_empty_normal_and_size_limit():
    assert preview_markdown(PreviewRequest(markdown="")) == {"html": ""}
    assert "<h2>Rede</h2>" in preview_markdown(PreviewRequest(markdown="## Rede"))["html"]
    with pytest.raises(HTTPException) as error:
        preview_markdown(PreviewRequest(markdown="a" * (MAX_MARKDOWN_INPUT_BYTES + 1)))
    assert error.value.status_code == 413


def test_preview_does_not_change_speech_plan():
    markdown = "# Redes\n\nHTTPS usa **TLS**.\n\n- Primeiro\n- Segundo"
    before = build_speech_plan(parse_markdown(markdown))
    render_markdown_preview(markdown)
    after = build_speech_plan(parse_markdown(markdown))
    assert after == before
