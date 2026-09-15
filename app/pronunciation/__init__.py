from app.pronunciation.resolver import (
    AppliedPronunciationRule,
    PronunciationResolver,
    PronunciationResult,
    ResolvedSpeechPlan,
    resolve_speech_plan,
)
from app.pronunciation.pt_br import (
    CONTROLLED_ACRONYMS,
    PT_BR_ENTRIES,
    PT_BR_CANONICAL_LETTER_NAMES,
    PT_BR_PROFILE,
    PT_BR_RESOLVER,
    PT_BR_TTS_LETTER_CUES,
    spell_for_tts,
)

__all__ = [
    "AppliedPronunciationRule",
    "PronunciationResolver",
    "PronunciationResult",
    "ResolvedSpeechPlan",
    "resolve_speech_plan",
    "PT_BR_PROFILE",
    "PT_BR_RESOLVER",
    "PT_BR_TTS_LETTER_CUES",
    "PT_BR_CANONICAL_LETTER_NAMES",
    "PT_BR_ENTRIES",
    "CONTROLLED_ACRONYMS",
    "spell_for_tts",
]
