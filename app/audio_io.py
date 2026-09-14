from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from app.config import (
    MP3_BITRATE,
)


@dataclass(frozen=True)
class AudioTimelineEntry:
    index: int

    kind: str

    text: str

    start_seconds: float

    end_seconds: float

    pause_after_ms: int


def as_audio_tensor(
    audio,
) -> torch.Tensor:
    if not torch.is_tensor(
        audio
    ):
        audio = torch.as_tensor(
            audio
        )

    audio = (
        audio
        .detach()
        .to(torch.float32)
        .cpu()
    )

    if audio.ndim == 1:
        audio = (
            audio.unsqueeze(0)
        )

    if audio.ndim != 2:
        raise RuntimeError(
            "Formato de áudio inválido: "
            f"{tuple(audio.shape)}"
        )

    return audio


def stereo_silence(
    milliseconds: int,
    sample_rate: int,
    channels: int,
) -> torch.Tensor:
    samples = int(
        sample_rate
        * milliseconds
        / 1000
    )

    return torch.zeros(
        (
            channels,
            samples,
        ),
        dtype=torch.float32,
    )


def combine_audio(
    decoded_units: list[
        tuple[
            int,
            str,
            str,
            int,
            torch.Tensor,
        ]
    ],
    sample_rate: int,
) -> tuple[
    torch.Tensor,
    list[AudioTimelineEntry],
]:
    parts: list[
        torch.Tensor
    ] = []

    timeline: list[
        AudioTimelineEntry
    ] = []

    current_sample = 0

    for (
        index,
        kind,
        text,
        pause_after_ms,
        raw_audio,
    ) in decoded_units:
        audio = as_audio_tensor(
            raw_audio
        )

        channels = int(
            audio.shape[0]
        )

        start_sample = (
            current_sample
        )

        end_sample = (
            start_sample
            + audio.shape[-1]
        )

        timeline.append(
            AudioTimelineEntry(
                index=index,
                kind=kind,
                text=text,
                start_seconds=(
                    start_sample
                    / sample_rate
                ),
                end_seconds=(
                    end_sample
                    / sample_rate
                ),
                pause_after_ms=(
                    pause_after_ms
                ),
            )
        )

        parts.append(
            audio
        )

        current_sample = (
            end_sample
        )

        pause = stereo_silence(
            pause_after_ms,
            sample_rate,
            channels,
        )

        parts.append(
            pause
        )

        current_sample += (
            pause.shape[-1]
        )

    if not parts:
        raise RuntimeError(
            "Nenhum áudio para combinar."
        )

    return (
        torch.cat(
            parts,
            dim=-1,
        ),
        timeline,
    )


def export_mp3(
    audio: torch.Tensor,
    sample_rate: int,
    destination: Path,
) -> None:
    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    data = (
        audio
        .transpose(0, 1)
        .contiguous()
        .numpy()
    )

    with tempfile.TemporaryDirectory(
        prefix="markdown_tts_"
    ) as temporary_directory:
        wav_file = (
            Path(
                temporary_directory
            )
            / "master.wav"
        )

        sf.write(
            str(wav_file),
            data,
            sample_rate,
            subtype="PCM_16",
        )

        subprocess.run(
            [
                "ffmpeg",
                "-y",

                "-i",
                str(wav_file),

                "-codec:a",
                "libmp3lame",

                "-b:a",
                MP3_BITRATE,

                str(destination),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )