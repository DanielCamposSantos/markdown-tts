import pytest


@pytest.mark.gpu
@pytest.mark.slow
@pytest.mark.integration_tts
@pytest.mark.skip(reason="Opt-in GPU/MOSS test placeholder")
def test_moss_gpu_smoke_placeholder():
    pass
