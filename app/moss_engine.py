from __future__ import annotations

import gc
import random
import time
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf
import torch
from transformers import (
    AutoModel,
    AutoProcessor,
)

from app.audio_io import (
    AudioTimelineEntry,
    combine_audio,
    export_mp3,
)

from app.config import (
    AUDIO_REPETITION_PENALTY,
    AUDIO_TEMPERATURE,
    AUDIO_TOP_K,
    AUDIO_TOP_P,
    BASE_SEED,
    LANGUAGE,
    MAX_NEW_TOKENS,
    MODEL_ID,
    REFERENCE_AUDIO,
)

from app.domain.models import GenerationResult
from app.audio_generation_guard import (
    GenerationCancelled,
    generate_with_runaway_guard,
)

from app.speech_plan import (
    SpeechUnit,
)


ProgressCallback = Callable[
    [str, int, int, str],
    None,
]


def cleanup_cuda() -> None:
    gc.collect()

    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def set_seed(
    seed: int,
) -> None:
    random.seed(seed)
    np.random.seed(seed)

    torch.manual_seed(seed)

    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(
            seed
        )


def move_output_to_cpu(
    output,
):
    result = []

    for (
        start_length,
        generation_ids,
    ) in output:
        result.append(
            (
                int(start_length),
                generation_ids
                .detach()
                .cpu(),
            )
        )

    return result


class MossEngine:
    def __init__(
        self,
    ) -> None:
        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA não está disponível."
            )

        if not REFERENCE_AUDIO.exists():
            raise FileNotFoundError(
                "Voz canônica não encontrada: "
                f"{REFERENCE_AUDIO}"
            )

        torch.backends.cuda.enable_cudnn_sdp(
            False
        )

        torch.backends.cuda.enable_flash_sdp(
            True
        )

        torch.backends.cuda.enable_mem_efficient_sdp(
            True
        )

        torch.backends.cuda.enable_math_sdp(
            True
        )

        print(
            "Carregando processor MOSS..."
        )

        self.processor = (
            AutoProcessor.from_pretrained(
                MODEL_ID,
                trust_remote_code=True,
            )
        )

        self.reference_codes = (
            self._encode_reference()
        )

        print(
            "MOSS preparado."
        )

    def _load_reference(
        self,
    ) -> tuple[
        torch.Tensor,
        int,
    ]:
        data, sample_rate = sf.read(
            str(
                REFERENCE_AUDIO
            ),
            dtype="float32",
            always_2d=True,
        )

        waveform = (
            torch.from_numpy(
                data.T.copy()
            )
        )

        return (
            waveform,
            int(sample_rate),
        )

    def _encode_reference(
        self,
    ) -> torch.Tensor:
        print(
            "Codificando voz canônica..."
        )

        waveform, sample_rate = (
            self._load_reference()
        )

        self.processor.audio_tokenizer = (
            self.processor
            .audio_tokenizer
            .to("cuda:0")
        )

        with torch.inference_mode():
            reference_codes = (
                self.processor
                .encode_audios_from_wav(
                    [waveform],
                    sampling_rate=(
                        sample_rate
                    ),
                )[0]
            )

        reference_codes = (
            reference_codes
            .detach()
            .cpu()
            .long()
        )

        self.processor.audio_tokenizer = (
            self.processor
            .audio_tokenizer
            .to("cpu")
        )

        del waveform

        cleanup_cuda()

        print(
            "Voz canônica pronta."
        )

        return reference_codes

    def _load_model(
        self,
    ):
        model = (
            AutoModel.from_pretrained(
                MODEL_ID,
                trust_remote_code=True,
                dtype=torch.bfloat16,
                attn_implementation=(
                    "sdpa"
                ),
                low_cpu_mem_usage=True,
            )
            .to("cuda:0")
        )

        model.eval()

        return model

    def _generate_unit(
        self,
        model,
        unit: SpeechUnit,
        seed: int | None = None,
    ):
        conversation = [
            [
                self.processor
                .build_user_message(
                    text=(
                        unit
                        .synthesis_text
                    ),

                    reference=[
                        self.reference_codes
                    ],

                    language=LANGUAGE,
                )
            ]
        ]

        batch = self.processor(
            conversation,
            mode="generation",
        )

        input_ids = (
            batch["input_ids"]
            .to("cuda:0")
        )

        attention_mask = (
            batch["attention_mask"]
            .to("cuda:0")
        )

        set_seed(BASE_SEED + unit.index if seed is None else seed)

        with torch.inference_mode():
            output = model.generate(
                input_ids=input_ids,

                attention_mask=(
                    attention_mask
                ),

                max_new_tokens=(
                    MAX_NEW_TOKENS
                ),

                do_sample=True,

                audio_temperature=(
                    AUDIO_TEMPERATURE
                ),

                audio_top_p=(
                    AUDIO_TOP_P
                ),

                audio_top_k=(
                    AUDIO_TOP_K
                ),

                audio_repetition_penalty=(
                    AUDIO_REPETITION_PENALTY
                ),
            )

        output = move_output_to_cpu(
            output
        )

        del batch
        del input_ids
        del attention_mask

        cleanup_cuda()

        return output

    def _decode_output(
        self,
        output,
    ) -> torch.Tensor:
        messages = list(
            self.processor.decode(
                output
            )
        )

        for message in messages:
            if (
                message is not None
                and message.audio_codes_list
            ):
                audio = (
                    message
                    .audio_codes_list[0]
                )

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

                return audio

        raise RuntimeError(
            "MOSS não retornou áudio."
        )

    def generate(
        self,
        units: list[SpeechUnit],
        output_file: Path,
        progress_callback:
            ProgressCallback
            | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> GenerationResult:
        if not units:
            raise ValueError(
                "Speech Plan vazio."
            )

        total = len(
            units
        )

        if progress_callback:
            progress_callback(
                "model",
                0,
                total,
                "Carregando MOSS-TTS...",
            )

        model = self._load_model()

        generated_outputs = []

        generation_start = (
            time.perf_counter()
        )

        try:
            for position, unit in enumerate(
                units,
                start=1,
            ):
                if should_cancel and should_cancel():
                    raise GenerationCancelled("Geração cancelada.")
                if progress_callback:
                    progress_callback(
                        "generation",
                        position,
                        total,
                        (
                            f"Gerando frase "
                            f"{position} de {total}"
                        ),
                    )

                print()
                print(
                    f"[{position}/{total}] "
                    f"{unit.kind}"
                )

                print(
                    unit.display_text
                )

                output = generate_with_runaway_guard(
                    unit=unit,
                    generate_attempt=lambda seed: self._generate_unit(
                        model, unit, seed=seed
                    ),
                    audio_pad_token_id=int(
                        self.processor.model_config.audio_pad_token_id
                    ),
                    first_seed=BASE_SEED + unit.index,
                    should_cancel=should_cancel,
                )

                generated_outputs.append(
                    (
                        unit,
                        output,
                    )
                )

        finally:
            del model

            cleanup_cuda()

        generation_seconds = (
            time.perf_counter()
            - generation_start
        )

        if progress_callback:
            progress_callback(
                "decode",
                0,
                total,
                "Preparando decoder...",
            )

        self.processor.audio_tokenizer = (
            self.processor
            .audio_tokenizer
            .to("cuda:0")
        )

        decoded_units = []

        decode_start = (
            time.perf_counter()
        )

        try:
            for position, (
                unit,
                output,
            ) in enumerate(
                generated_outputs,
                start=1,
            ):
                if should_cancel and should_cancel():
                    raise GenerationCancelled("Geração cancelada.")
                if progress_callback:
                    progress_callback(
                        "decode",
                        position,
                        total,
                        (
                            f"Decodificando "
                            f"{position} de {total}"
                        ),
                    )

                audio = (
                    self._decode_output(
                        output
                    )
                )

                decoded_units.append(
                    (
                        unit.index,
                        unit.kind,
                        unit.display_text,
                        unit.pause_after_ms,
                        audio,
                    )
                )

        finally:
            self.processor.audio_tokenizer = (
                self.processor
                .audio_tokenizer
                .to("cpu")
            )

            cleanup_cuda()

        decode_seconds = (
            time.perf_counter()
            - decode_start
        )

        sample_rate = int(
            self.processor
            .model_config
            .sampling_rate
        )

        master_audio, timeline = (
            combine_audio(
                decoded_units,
                sample_rate,
            )
        )

        if should_cancel and should_cancel():
            raise GenerationCancelled("Geração cancelada.")

        if progress_callback:
            progress_callback(
                "export",
                total,
                total,
                "Criando MP3...",
            )

        export_mp3(
            master_audio,
            sample_rate,
            output_file,
        )

        duration_seconds = (
            master_audio.shape[-1]
            / sample_rate
        )

        return GenerationResult(
            output_file=(
                output_file.resolve()
            ),

            duration_seconds=(
                duration_seconds
            ),

            generation_seconds=(
                generation_seconds
            ),

            decode_seconds=(
                decode_seconds
            ),

            timeline=tuple(
                timeline
            ),
        )
