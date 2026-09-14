from app.markdown_parser import (
    MarkdownBlock,
    normalize_whitespace,
    parse_markdown,
)


def test_parse_heading_and_paragraph():
    blocks = parse_markdown(
        "# Titulo\n\nEste e um paragrafo."
    )

    assert blocks == [
        MarkdownBlock(
            kind="heading",
            text="Titulo",
            level=1,
        ),
        MarkdownBlock(
            kind="paragraph",
            text="Este e um paragrafo.",
        ),
    ]


def test_parse_multiple_paragraphs():
    blocks = parse_markdown(
        "Primeiro paragrafo.\n\nSegundo paragrafo."
    )

    assert [block.kind for block in blocks] == [
        "paragraph",
        "paragraph",
    ]
    assert [block.text for block in blocks] == [
        "Primeiro paragrafo.",
        "Segundo paragrafo.",
    ]


def test_parse_bullet_and_ordered_lists():
    bullet, ordered = parse_markdown(
        "- Um\n- Dois\n\n1. Primeiro\n2. Segundo"
    )

    assert bullet.kind == "list"
    assert bullet.items == ("Um", "Dois")
    assert bullet.ordered is False
    assert ordered.kind == "list"
    assert ordered.items == ("Primeiro", "Segundo")
    assert ordered.ordered is True


def test_parse_blockquote():
    blocks = parse_markdown(
        "> Uma citacao\n> em duas linhas."
    )

    assert blocks == [
        MarkdownBlock(
            kind="blockquote",
            text="Uma citacao em duas linhas.",
        )
    ]


def test_parse_inline_code_emphasis_and_link_content():
    blocks = parse_markdown(
        "**forte** e *enfase* com `codigo` e [link](https://example.com)."
    )

    assert blocks[0].text == (
        "forte e enfase com codigo e link."
    )


def test_parse_soft_line_break_and_whitespace_normalization():
    blocks = parse_markdown(
        "Uma linha\nquebrada\n\n  outra   parte  "
    )

    assert [block.text for block in blocks] == [
        "Uma linha quebrada",
        "outra parte",
    ]
    assert normalize_whitespace(
        "  muitas\tpalavras\n  juntas  "
    ) == "muitas palavras juntas"


def test_parse_fenced_code_as_code_block():
    blocks = parse_markdown(
        "```python\nprint('ola')\n```"
    )

    assert blocks == [
        MarkdownBlock(
            kind="code",
            text="print('ola')",
        )
    ]


def test_inline_html_content_is_preserved_by_current_parser():
    blocks = parse_markdown(
        "Texto <span>visivel</span> final."
    )

    assert blocks[0].text == "Texto visivel final."
