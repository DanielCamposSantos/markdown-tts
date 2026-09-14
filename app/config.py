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

LANGUAGE = "Portuguese"

SAMPLE_RATE = 48000

MAX_NEW_TOKENS = 1000

AUDIO_TEMPERATURE = 1.7
AUDIO_TOP_P = 0.8
AUDIO_TOP_K = 25
AUDIO_REPETITION_PENALTY = 1.0

BASE_SEED = 8300

MP3_BITRATE = "192k"