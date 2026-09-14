import os
from pathlib import Path


ROOT = (
    Path(__file__)
    .resolve()
    .parent
    .parent
)

MODEL_ID = (
    "OpenMOSS-Team/"
    "MOSS-TTS-Local-Transformer-v1.5"
)

REFERENCE_AUDIO = (
    ROOT
    / "voices"
    / "narrator_reference.wav"
)

OUTPUTS_DIR = (
    ROOT
    / "outputs"
)

TEMP_DIR = (
    ROOT
    / "temp"
)

LIBRARY_DIR = (
    ROOT
    / "library"
)

LANGUAGE = "Portuguese"

SAMPLE_RATE = 48000

MAX_NEW_TOKENS = 1000

AUDIO_TEMPERATURE = 1.7
AUDIO_TOP_P = 0.8
AUDIO_TOP_K = 25
AUDIO_REPETITION_PENALTY = 1.0

BASE_SEED = 8300

MP3_BITRATE = "192k"

MODEL_IDLE_TIMEOUT_SECONDS = 300.0

ASR_VALIDATION_ENABLED = os.environ.get("MARKDOWN_TTS_ASR_VALIDATION", "0") == "1"
ASR_BACKEND = "faster-whisper"
ASR_MODEL_PATH = Path(os.environ.get(
    "MARKDOWN_TTS_ASR_MODEL_PATH",
    str(ROOT / "benchmarks" / "models" / "faster-whisper-medium"),
))
ASR_COMPUTE_TYPE = "int8_float16"
ASR_LANGUAGE = "pt"
ASR_BEAM_SIZE = 5
ASR_MAX_AUTO_REGENERATION_ROUNDS = 2
ASR_AUTO_REGENERATION_SEED_OFFSET = 1_000_001
