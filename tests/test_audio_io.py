import torch

from app.audio_io import combine_audio


def test_combine_audio_preserves_order_pauses_and_timeline():
    decoded_units = [
        (
            1,
            "paragraph",
            "Primeiro.",
            100,
            torch.tensor([[1.0, 1.0]]),
        ),
        (
            2,
            "paragraph",
            "Segundo.",
            200,
            torch.tensor([[2.0, 2.0, 2.0]]),
        ),
    ]

    audio, timeline = combine_audio(
        decoded_units,
        sample_rate=1000,
    )

    assert audio.shape == (1, 305)
    assert audio[0, :2].tolist() == [1.0, 1.0]
    assert torch.count_nonzero(audio[0, 2:102]) == 0
    assert audio[0, 102:105].tolist() == [2.0, 2.0, 2.0]
    assert torch.count_nonzero(audio[0, 105:]) == 0

    assert [entry.index for entry in timeline] == [1, 2]
    assert [entry.text for entry in timeline] == [
        "Primeiro.",
        "Segundo.",
    ]
    assert timeline[0].start_seconds == 0.0
    assert timeline[0].end_seconds == 0.002
    assert timeline[0].pause_after_ms == 100
    assert timeline[1].start_seconds == 0.102
    assert timeline[1].end_seconds == 0.105
    assert timeline[1].pause_after_ms == 200
    assert audio.shape[-1] / 1000 == 0.305


def test_combine_audio_accepts_one_dimensional_audio():
    audio, timeline = combine_audio(
        [
            (
                7,
                "code",
                "linha",
                0,
                torch.tensor([3.0, 4.0]),
            )
        ],
        sample_rate=1000,
    )

    assert audio.shape == (1, 2)
    assert audio[0].tolist() == [3.0, 4.0]
    assert timeline[0].start_seconds == 0.0
    assert timeline[0].end_seconds == 0.002
