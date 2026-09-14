import pytest

from app.markdown_parser import parse_markdown
from app.speech_plan import (
    canonical_blocks,
    canonical_units,
    build_speech_plan,
    split_sentences,
)


def test_split_single_sentence_and_missing_terminal_punctuation():
    assert split_sentences("Uma frase") == ["Uma frase"]
    assert split_sentences("Uma frase.") == ["Uma frase."]


def test_split_multiple_sentence_terminators():
    assert split_sentences(
        "Primeira. Segunda! Terceira? Quarta."
    ) == [
        "Primeira.",
        "Segunda!",
        "Terceira?",
        "Quarta.",
    ]


def test_split_normalizes_extra_spaces_and_closing_quote():
    assert split_sentences(
        '  Ele   chegou."   Depois saiu.  '
    ) == [
        'Ele chegou."',
        "Depois saiu.",
    ]


@pytest.mark.parametrize(
    "abbreviation",
    [
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
    ],
)
def test_split_supported_abbreviations(abbreviation):
    assert split_sentences(
        f"{abbreviation} chegou. Proxima frase."
    ) == [
        f"{abbreviation} chegou.",
        "Proxima frase.",
    ]


def test_https_tls_sentence_stays_integral_in_one_unit():
    text = "O HTTPS utiliza TLS para proteger a comunicação."
    units = build_speech_plan(
        parse_markdown(text)
    )

    assert len(units) == 1
    assert units[0].display_text == text
    assert units[0].synthesis_text == text


def test_technical_acronyms_are_preserved_without_pronunciation_changes():
    text = "SYN SYN-ACK ACK."
    units = build_speech_plan(
        parse_markdown(text)
    )

    assert units[0].display_text == text
    assert units[0].synthesis_text == text
    assert units[0].source_atoms == (text,)


def test_plan_preserves_order_and_content():
    markdown = (
        "# Secao\n\n"
        "Primeira frase. Segunda frase.\n\n"
        "> Uma observacao.\n\n"
        "- Item A\n- Item B"
    )
    blocks = parse_markdown(markdown)
    units = build_speech_plan(blocks)

    assert canonical_units(units) == canonical_blocks(blocks)
    assert [unit.index for unit in units] == list(
        range(1, len(units) + 1)
    )
    assert [unit.display_text for unit in units] == [
        "Secao",
        "Primeira frase.",
        "Segunda frase.",
        "Uma observacao.",
        "• Item A\n• Item B",
    ]


def test_plan_links_previous_and_next_units():
    units = build_speech_plan(
        parse_markdown(
            "Primeira. Segunda. Terceira."
        )
    )

    assert units[0].previous_id is None
    assert units[0].next_id == units[1].index
    assert units[1].previous_id == units[0].index
    assert units[1].next_id == units[2].index
    assert units[2].previous_id == units[1].index
    assert units[2].next_id is None


def test_plan_structural_ids_and_sentence_indexes():
    units = build_speech_plan(
        parse_markdown(
            "# Titulo\n\nUma. Duas."
        )
    )

    heading, first, second = units
    assert heading.section_id == heading.index
    assert first.section_id == heading.index
    assert second.section_id == heading.index
    assert first.paragraph_id == second.paragraph_id
    assert first.sentence_index == 1
    assert second.sentence_index == 2


def test_contextual_list_keeps_intro_and_items_in_one_unit():
    units = build_speech_plan(
        parse_markdown(
            "Itens importantes:\n\n- SYN\n- SYN-ACK\n- ACK"
        )
    )

    assert len(units) == 1
    unit = units[0]
    assert unit.kind == "list"
    assert unit.display_text == (
        "Itens importantes:\n\n"
        "• SYN\n• SYN-ACK\n• ACK"
    )
    assert unit.source_atoms == (
        "Itens importantes:",
        "SYN",
        "SYN-ACK",
        "ACK",
    )
    assert "[pause 0.4s]" in unit.synthesis_text
    assert "[pause 0.35s]" in unit.synthesis_text
    assert unit.pause_after_ms == 500


def test_isolated_list_block_is_one_speech_unit():
    units = build_speech_plan(
        parse_markdown("- Primeiro\n- Segundo")
    )

    assert len(units) == 1
    assert units[0].kind == "list"
    assert units[0].display_text == "• Primeiro\n• Segundo"
    assert units[0].synthesis_text == (
        "Primeiro. [pause 0.35s] Segundo."
    )


def test_blockquote_and_code_units_keep_content():
    units = build_speech_plan(
        parse_markdown(
            "> Nota importante.\n\n```\nlinha 1\n```"
        )
    )

    assert [unit.kind for unit in units] == [
        "blockquote",
        "code",
    ]
    assert units[0].display_text == "Nota importante."
    assert units[1].display_text == "linha 1"
    assert canonical_units(units) == "Nota importante. linha 1"


def test_pause_defaults_are_characterized():
    units = build_speech_plan(
        parse_markdown(
            "# Titulo\n\nParagrafo.\n\n> Citacao.\n\n```\ncode\n```"
        )
    )

    assert [unit.pause_after_ms for unit in units] == [
        500,
        250,
        300,
        500,
    ]
