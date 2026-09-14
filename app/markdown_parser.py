from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from markdown_it import MarkdownIt


BlockKind = Literal[
    "heading",
    "paragraph",
    "list",
    "blockquote",
    "code",
]


@dataclass(frozen=True)
class MarkdownBlock:
    kind: BlockKind

    text: str = ""

    level: int | None = None

    items: tuple[str, ...] = ()

    ordered: bool = False


def normalize_whitespace(
    text: str,
) -> str:
    return " ".join(
        text.split()
    )


def inline_text(token) -> str:
    parts: list[str] = []

    for child in token.children or []:
        if child.type in {
            "text",
            "code_inline",
        }:
            parts.append(
                child.content
            )

        elif child.type in {
            "softbreak",
            "hardbreak",
        }:
            parts.append(" ")

        elif child.type == "image":
            if child.content:
                parts.append(
                    child.content
                )

        elif child.type in {
            "strong_open",
            "strong_close",
            "em_open",
            "em_close",
            "link_open",
            "link_close",
            "s_open",
            "s_close",
        }:
            continue

        elif child.type == "html_inline":
            continue

        elif child.content:
            parts.append(
                child.content
            )

    return normalize_whitespace(
        "".join(parts)
    )


def consume_list(
    tokens,
    start_index: int,
) -> tuple[MarkdownBlock, int]:
    opening = (
        tokens[start_index]
    )

    ordered = (
        opening.type
        == "ordered_list_open"
    )

    depth = 0

    current_parts: list[str] = []
    items: list[str] = []

    inside_item = False

    index = start_index

    while index < len(tokens):
        token = tokens[index]

        if token.type in {
            "bullet_list_open",
            "ordered_list_open",
        }:
            depth += 1

        elif token.type in {
            "bullet_list_close",
            "ordered_list_close",
        }:
            depth -= 1

            if depth == 0:
                return (
                    MarkdownBlock(
                        kind="list",
                        items=tuple(items),
                        ordered=ordered,
                    ),
                    index + 1,
                )

        elif (
            token.type
            == "list_item_open"
            and depth == 1
        ):
            current_parts = []
            inside_item = True

        elif (
            token.type
            == "list_item_close"
            and depth == 1
        ):
            text = (
                normalize_whitespace(
                    " ".join(
                        current_parts
                    )
                )
            )

            if text:
                items.append(text)

            current_parts = []
            inside_item = False

        elif (
            token.type == "inline"
            and inside_item
        ):
            text = inline_text(
                token
            )

            if text:
                current_parts.append(
                    text
                )

        index += 1

    raise ValueError(
        "Lista Markdown não foi "
        "fechada corretamente."
    )


def consume_blockquote(
    tokens,
    start_index: int,
) -> tuple[MarkdownBlock, int]:
    depth = 0

    parts: list[str] = []

    index = start_index

    while index < len(tokens):
        token = tokens[index]

        if (
            token.type
            == "blockquote_open"
        ):
            depth += 1

        elif (
            token.type
            == "blockquote_close"
        ):
            depth -= 1

            if depth == 0:
                return (
                    MarkdownBlock(
                        kind="blockquote",
                        text=normalize_whitespace(
                            " ".join(parts)
                        ),
                    ),
                    index + 1,
                )

        elif token.type == "inline":
            text = inline_text(
                token
            )

            if text:
                parts.append(text)

        index += 1

    raise ValueError(
        "Blockquote não foi fechado."
    )


def parse_markdown(
    markdown: str,
) -> list[MarkdownBlock]:
    parser = MarkdownIt(
        "commonmark"
    )

    tokens = parser.parse(
        markdown
    )

    blocks: list[
        MarkdownBlock
    ] = []

    index = 0

    while index < len(tokens):
        token = tokens[index]

        if (
            token.type
            == "heading_open"
        ):
            level = int(
                token.tag[1:]
            )

            if (
                index + 1
                < len(tokens)
                and tokens[
                    index + 1
                ].type
                == "inline"
            ):
                text = inline_text(
                    tokens[
                        index + 1
                    ]
                )

                if text:
                    blocks.append(
                        MarkdownBlock(
                            kind="heading",
                            text=text,
                            level=level,
                        )
                    )

            index += 3
            continue

        if (
            token.type
            == "paragraph_open"
        ):
            if (
                index + 1
                < len(tokens)
                and tokens[
                    index + 1
                ].type
                == "inline"
            ):
                text = inline_text(
                    tokens[
                        index + 1
                    ]
                )

                if text:
                    blocks.append(
                        MarkdownBlock(
                            kind="paragraph",
                            text=text,
                        )
                    )

            index += 3
            continue

        if token.type in {
            "bullet_list_open",
            "ordered_list_open",
        }:
            block, index = (
                consume_list(
                    tokens,
                    index,
                )
            )

            blocks.append(block)
            continue

        if (
            token.type
            == "blockquote_open"
        ):
            block, index = (
                consume_blockquote(
                    tokens,
                    index,
                )
            )

            if block.text:
                blocks.append(
                    block
                )

            continue

        if token.type in {
            "fence",
            "code_block",
        }:
            text = (
                token.content
                .strip()
            )

            if text:
                blocks.append(
                    MarkdownBlock(
                        kind="code",
                        text=text,
                    )
                )

            index += 1
            continue

        index += 1

    return blocks


def source_atoms(
    blocks: list[MarkdownBlock],
) -> list[str]:
    atoms: list[str] = []

    for block in blocks:
        if block.kind == "list":
            atoms.extend(
                block.items
            )

        elif block.text:
            atoms.append(
                block.text
            )

    return atoms