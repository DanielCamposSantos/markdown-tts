from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from app.markdown_parser import MarkdownBlock


SpeechKind = Literal[
    "heading",
    "paragraph",
    "list",
    "blockquote",
    "code",
]


@dataclass(frozen=True)
class SpeechUnit:
    index: int

    kind: SpeechKind

    display_text: str

    synthesis_text: str

    source_atoms: tuple[str, ...]

    pause_after_ms: int

    previous_id: int | None = None

    next_id: int | None = None

    section_id: int | None = None

    paragraph_id: int | None = None

    sentence_index: int | None = None


@dataclass(frozen=True)
class SpeechContext:
    current: SpeechUnit

    previous: SpeechUnit | None

    next: SpeechUnit | None

    section: SpeechUnit | None


ABBREVIATIONS = {
    "sr.",
    "sra.",
    "srta.",
    "dr.",
    "dra.",
    "prof.",
    "profa.",
    "etc.",
    "ex.",
    "fig.",
    "art.",
    "pág.",
    "aprox.",
}


def normalize_whitespace(
    text: str,
) -> str:
    return " ".join(
        text.split()
    )


def terminal(
    text: str,
) -> str:
    text = text.strip()

    if not text:
        return text

    if text[-1] in ".!?;:":
        return text

    return text + "."


def previous_word(
    text: str,
    position: int,
) -> str:
    start = position

    while (
        start > 0
        and not text[start - 1].isspace()
    ):
        start -= 1

    return (
        text[start:position + 1]
        .strip()
        .lower()
    )


def split_sentences(
    text: str,
) -> list[str]:
    text = normalize_whitespace(
        text
    )

    if not text:
        return []

    sentences: list[str] = []

    start = 0
    index = 0

    while index < len(text):
        char = text[index]

        if char not in ".!?":
            index += 1
            continue

        if (
            char == "."
            and previous_word(
                text,
                index,
            ) in ABBREVIATIONS
        ):
            index += 1
            continue

        end = index + 1

        while (
            end < len(text)
            and text[end] in ".!?"
        ):
            end += 1

        while (
            end < len(text)
            and text[end] in "\"'”’)]}"
        ):
            end += 1

        if (
            end < len(text)
            and not text[end].isspace()
        ):
            index = end
            continue

        sentence = (
            text[start:end]
            .strip()
        )

        if sentence:
            sentences.append(
                sentence
            )

        while (
            end < len(text)
            and text[end].isspace()
        ):
            end += 1

        start = end
        index = end

    remainder = (
        text[start:]
        .strip()
    )

    if remainder:
        sentences.append(
            remainder
        )

    return sentences


def render_list_display(
    items: tuple[str, ...],
    ordered: bool,
) -> str:
    lines: list[str] = []

    for index, item in enumerate(
        items,
        start=1,
    ):
        if ordered:
            prefix = f"{index}."
        else:
            prefix = "•"

        lines.append(
            f"{prefix} {item}"
        )

    return "\n".join(
        lines
    )


def render_list_synthesis(
    items: tuple[str, ...],
) -> str:
    rendered: list[str] = []

    for item in items:
        rendered.append(
            terminal(item)
        )

    return (
        " [pause 0.35s] "
    ).join(
        rendered
    )


def canonical_blocks(
    blocks: list[MarkdownBlock],
) -> str:
    parts: list[str] = []

    for block in blocks:
        if block.kind == "list":
            parts.extend(
                block.items
            )

        elif block.text:
            parts.append(
                block.text
            )

    return normalize_whitespace(
        " ".join(parts)
    )


def canonical_units(
    units: list[SpeechUnit],
) -> str:
    parts: list[str] = []

    for unit in units:
        parts.extend(
            unit.source_atoms
        )

    return normalize_whitespace(
        " ".join(parts)
    )


def validate_content(
    blocks: list[MarkdownBlock],
    units: list[SpeechUnit],
) -> None:
    original = canonical_blocks(
        blocks
    )

    planned = canonical_units(
        units
    )

    if original != planned:
        raise RuntimeError(
            "O Speech Plan perdeu, "
            "alterou ou reordenou conteúdo.\n\n"
            f"ORIGINAL:\n{original}\n\n"
            f"PLANEJADO:\n{planned}"
        )


def link_units(
    units: list[SpeechUnit],
) -> list[SpeechUnit]:
    linked: list[SpeechUnit] = []

    for position, unit in enumerate(
        units
    ):
        previous_id = None
        next_id = None

        if position > 0:
            previous_id = (
                units[
                    position - 1
                ].index
            )

        if (
            position + 1
            < len(units)
        ):
            next_id = (
                units[
                    position + 1
                ].index
            )

        linked.append(
            replace(
                unit,
                previous_id=previous_id,
                next_id=next_id,
            )
        )

    return linked


def validate_links(
    units: list[SpeechUnit],
) -> None:
    by_id = {
        unit.index: unit
        for unit in units
    }

    for unit in units:
        if unit.previous_id is not None:
            previous = by_id[
                unit.previous_id
            ]

            if (
                previous.next_id
                != unit.index
            ):
                raise RuntimeError(
                    "Ligação previous/next "
                    "inconsistente."
                )

        if unit.next_id is not None:
            next_unit = by_id[
                unit.next_id
            ]

            if (
                next_unit.previous_id
                != unit.index
            ):
                raise RuntimeError(
                    "Ligação next/previous "
                    "inconsistente."
                )


def get_context(
    units: list[SpeechUnit],
    unit_id: int,
) -> SpeechContext:
    by_id = {
        unit.index: unit
        for unit in units
    }

    current = by_id[
        unit_id
    ]

    previous = None
    next_unit = None
    section = None

    if current.previous_id is not None:
        previous = by_id.get(
            current.previous_id
        )

    if current.next_id is not None:
        next_unit = by_id.get(
            current.next_id
        )

    if current.section_id is not None:
        section = by_id.get(
            current.section_id
        )

    return SpeechContext(
        current=current,
        previous=previous,
        next=next_unit,
        section=section,
    )


def build_speech_plan(
    blocks: list[MarkdownBlock],
) -> list[SpeechUnit]:
    units: list[SpeechUnit] = []

    block_index = 0

    current_section_id: int | None = None

    paragraph_counter = 0

    def append(
        kind: SpeechKind,
        display: str,
        synthesis: str,
        atoms: list[str],
        pause_after_ms: int,
        section_id: int | None,
        paragraph_id: int | None = None,
        sentence_index: int | None = None,
    ) -> SpeechUnit:
        display = display.strip()
        synthesis = synthesis.strip()

        if not display:
            raise RuntimeError(
                "Tentativa de criar "
                "SpeechUnit sem texto."
            )

        if not synthesis:
            raise RuntimeError(
                "Tentativa de criar "
                "SpeechUnit sem síntese."
            )

        unit = SpeechUnit(
            index=len(units) + 1,
            kind=kind,
            display_text=display,
            synthesis_text=synthesis,
            source_atoms=tuple(
                atoms
            ),
            pause_after_ms=(
                pause_after_ms
            ),
            section_id=section_id,
            paragraph_id=paragraph_id,
            sentence_index=sentence_index,
        )

        units.append(
            unit
        )

        return unit

    while (
        block_index
        < len(blocks)
    ):
        block = blocks[
            block_index
        ]

        if block.kind == "heading":
            next_id = (
                len(units) + 1
            )

            heading = append(
                kind="heading",
                display=block.text,
                synthesis=terminal(
                    block.text
                ),
                atoms=[
                    block.text
                ],
                pause_after_ms=500,
                section_id=next_id,
            )

            current_section_id = (
                heading.index
            )

            block_index += 1
            continue

        if block.kind == "paragraph":
            paragraph_counter += 1

            paragraph_id = (
                paragraph_counter
            )

            sentences = split_sentences(
                block.text
            )

            next_block = None

            if (
                block_index + 1
                < len(blocks)
            ):
                next_block = blocks[
                    block_index + 1
                ]

            has_contextual_list = (
                next_block is not None
                and next_block.kind
                == "list"
                and block.text
                .rstrip()
                .endswith(":")
            )

            if has_contextual_list:
                if not sentences:
                    sentences = [
                        block.text
                    ]

                for sentence_number, sentence in enumerate(
                    sentences[:-1],
                    start=1,
                ):
                    append(
                        kind="paragraph",
                        display=sentence,
                        synthesis=sentence,
                        atoms=[
                            sentence
                        ],
                        pause_after_ms=250,
                        section_id=(
                            current_section_id
                        ),
                        paragraph_id=(
                            paragraph_id
                        ),
                        sentence_index=(
                            sentence_number
                        ),
                    )

                intro = (
                    sentences[-1]
                )

                list_block = (
                    next_block
                )

                list_display = (
                    render_list_display(
                        list_block.items,
                        list_block.ordered,
                    )
                )

                list_synthesis = (
                    render_list_synthesis(
                        list_block.items
                    )
                )

                append(
                    kind="list",
                    display=(
                        f"{intro}\n\n"
                        f"{list_display}"
                    ),
                    synthesis=(
                        f"{terminal(intro)} "
                        f"[pause 0.4s] "
                        f"{list_synthesis}"
                    ),
                    atoms=[
                        intro,
                        *list_block.items,
                    ],
                    pause_after_ms=500,
                    section_id=(
                        current_section_id
                    ),
                    paragraph_id=(
                        paragraph_id
                    ),
                    sentence_index=(
                        len(sentences)
                    ),
                )

                block_index += 2
                continue

            for sentence_number, sentence in enumerate(
                sentences,
                start=1,
            ):
                append(
                    kind="paragraph",
                    display=sentence,
                    synthesis=sentence,
                    atoms=[
                        sentence
                    ],
                    pause_after_ms=250,
                    section_id=(
                        current_section_id
                    ),
                    paragraph_id=(
                        paragraph_id
                    ),
                    sentence_index=(
                        sentence_number
                    ),
                )

            block_index += 1
            continue

        if block.kind == "list":
            append(
                kind="list",
                display=(
                    render_list_display(
                        block.items,
                        block.ordered,
                    )
                ),
                synthesis=(
                    render_list_synthesis(
                        block.items
                    )
                ),
                atoms=list(
                    block.items
                ),
                pause_after_ms=500,
                section_id=(
                    current_section_id
                ),
            )

            block_index += 1
            continue

        if block.kind == "blockquote":
            paragraph_counter += 1

            paragraph_id = (
                paragraph_counter
            )

            sentences = (
                split_sentences(
                    block.text
                )
            )

            for sentence_number, sentence in enumerate(
                sentences,
                start=1,
            ):
                append(
                    kind="blockquote",
                    display=sentence,
                    synthesis=sentence,
                    atoms=[
                        sentence
                    ],
                    pause_after_ms=300,
                    section_id=(
                        current_section_id
                    ),
                    paragraph_id=(
                        paragraph_id
                    ),
                    sentence_index=(
                        sentence_number
                    ),
                )

            block_index += 1
            continue

        if block.kind == "code":
            append(
                kind="code",
                display=block.text,
                synthesis=block.text,
                atoms=[
                    block.text
                ],
                pause_after_ms=500,
                section_id=(
                    current_section_id
                ),
            )

            block_index += 1
            continue

        raise RuntimeError(
            "Tipo de bloco desconhecido: "
            f"{block.kind}"
        )

    validate_content(
        blocks,
        units,
    )

    units = link_units(
        units
    )

    validate_links(
        units
    )

    return units