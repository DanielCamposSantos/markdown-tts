from types import SimpleNamespace

import pytest

from app.progress import (
    ETA_CONSERVATIVE_MARGIN,
    PHASE_RANGES,
    ProgressPhase,
    ProgressTracker,
    estimate_unit_work,
    phase_percentage,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def units(*texts):
    return [SimpleNamespace(synthesis_text=text) for text in texts]


def test_canonical_phase_contract_and_ranges():
    assert [phase.value for phase in ProgressPhase] == [
        "queued", "preparing", "model_loading", "generation", "decode",
        "asr_validation", "assemble", "export", "publish", "completed", "failed",
        "cancelling", "cancelled", "interrupted",
    ]
    assert PHASE_RANGES[ProgressPhase.COMPLETED] == (100.0, 100.0)


@pytest.mark.parametrize("current,total", [(0, 5), (1, 5), (5, 5), (1, 1), (1, 1000)])
def test_unit_progress_is_bounded(current, total):
    progress, phase_progress = phase_percentage(ProgressPhase.GENERATION, current, total)
    assert 12 <= progress <= 68
    assert 0 <= phase_progress <= 100


def test_full_pipeline_is_monotonic_and_completed_is_100():
    events = []
    tracker = ProgressTracker(events.append)
    for args in [
        ("preparing", 0, 0), ("model", 0, 3),
        ("generation", 1, 3), ("generation", 2, 3),
        ("generation", 3, 3), ("decode", 0, 3),
        ("decode", 1, 3), ("decode", 3, 3),
        ("asr_validation", 1, 3), ("asr_validation", 3, 3),
        ("assemble", 3, 3), ("export", 3, 3),
        ("publish", 3, 3), ("completed", 3, 3),
    ]:
        tracker.report(*args, message="event")
    values = [event.progress for event in events]
    assert values == sorted(values)
    assert events[-1].progress == 100


def test_asr_correction_loop_is_monotonic_and_has_no_eta():
    events = []
    tracker = ProgressTracker(events.append)
    tracker.report("decode", 3, 3, "decoded")
    tracker.report("asr_validation", 1, 3, "validating")
    tracker.report("model_loading", 0, 1, "correcting")
    tracker.report("generation", 1, 1, "corrective")
    tracker.report("asr_validation", 1, 1, "revalidating")
    assert [event.progress for event in events] == sorted(event.progress for event in events)
    assert events[-1].eta_seconds is None


def test_retry_keeps_unit_and_does_not_regress():
    events = []
    tracker = ProgressTracker(events.append)
    tracker.report("generation", 2, 4, "Gerando unidade 2")
    tracker.report("generation", 2, 4, "Nova tentativa 2/3")
    assert events[1].current == events[0].current == 2
    assert events[1].progress == events[0].progress


def test_work_weights_long_text_more_than_heading_and_counts_contextual_pauses():
    heading, paragraph, contextual = units(
        "Introdução", " ".join(["palavra"] * 30),
        "Item um [pause 0.35s] Item dois [pause 0.4s] Item três",
    )
    assert estimate_unit_work(paragraph) > estimate_unit_work(heading)
    assert estimate_unit_work(contextual) > 6


def test_eta_is_null_until_three_completed_units():
    clock, events = FakeClock(), []
    tracker = ProgressTracker(events.append, clock=clock)
    tracker.set_units(units("um dois três quatro", " ".join(["x"] * 30), "oito palavras aqui para uma terceira unidade completa", "restante"))
    tracker.report("generation", 1, 4, "one")
    clock.advance(2)
    tracker.report("generation", 2, 4, "two")
    clock.advance(12)
    tracker.report("generation", 3, 4, "three")
    assert events[-1].eta_seconds is None


@pytest.mark.parametrize("first", ["curta", " ".join(["longa"] * 40)])
def test_first_unit_never_drives_eta_alone(first):
    clock, events = FakeClock(), []
    tracker = ProgressTracker(events.append, clock=clock)
    tracker.set_units(units(first, "segunda unidade", "terceira unidade", "quarta unidade"))
    tracker.report("generation", 1, 4, "")
    clock.advance(20)
    tracker.report("generation", 2, 4, "")
    assert events[-1].eta_seconds is None


def test_work_weighted_simulation_uses_remaining_work_not_unit_count():
    clock, events = FakeClock(), []
    tracker = ProgressTracker(events.append, clock=clock)
    tracker.set_units(units(
        " ".join(["u"] * 4), " ".join(["u"] * 30), " ".join(["u"] * 8),
        " ".join(["u"] * 35), " ".join(["u"] * 5),
    ))
    tracker.report("generation", 1, 5, "")
    for current, seconds in [(2, 2), (3, 12), (4, 3)]:
        clock.advance(seconds)
        tracker.report("generation", current, 5, "")
    assert events[-1].eta_seconds == pytest.approx(18.4)
    assert events[-1].eta_seconds != pytest.approx(2 * 3)


def test_eta_median_limits_single_outlier_and_disappears_after_generation():
    clock, events = FakeClock(), []
    tracker = ProgressTracker(events.append, clock=clock)
    tracker.set_units(units(*(["um dois três quatro"] * 6)))
    tracker.report("generation", 1, 6, "")
    for current, seconds in [(2, 2), (3, 2), (4, 40), (5, 2)]:
        clock.advance(seconds)
        tracker.report("generation", current, 6, "")
    assert events[-1].eta_seconds == pytest.approx(4 * 0.5 * 2 * ETA_CONSERVATIVE_MARGIN)
    tracker.report("decode", 0, 6, "")
    assert events[-1].eta_seconds is None


def test_retry_time_belongs_to_same_unit_without_advancing_total():
    clock, events = FakeClock(), []
    tracker = ProgressTracker(events.append, clock=clock)
    tracker.set_units(units(*(["um dois três quatro"] * 4)))
    tracker.report("generation", 1, 4, "attempt 1")
    clock.advance(4)
    tracker.report("generation", 1, 4, "attempt 2")
    clock.advance(2)
    tracker.report("generation", 2, 4, "unit 2")
    for current in (3, 4):
        clock.advance(4)
        tracker.report("generation", current, 4, "next")
    assert events[-1].current == 4
    assert events[-1].eta_seconds == pytest.approx(4 * ETA_CONSERVATIVE_MARGIN)


@pytest.mark.parametrize("text", ["", "!!!", "[pause 999999999999999999999999999999999999999999999999999999s]"])
def test_unit_work_is_finite_positive_for_edge_cases(text):
    work = estimate_unit_work(units(text)[0])
    assert 0 < work < float("inf")


def test_regeneration_is_one_unit_and_has_semantic_phases_without_eta():
    events = []
    tracker = ProgressTracker(events.append, operation="regenerate_unit", unit_id=17)
    for phase in ["generation", "decode", "assemble", "export", "publish"]:
        tracker.report(phase, 1, 1, "engine")
    assert events[0].message == "Regenerando unidade 17"
    assert events[0].current == events[0].total == 1
    assert events[2].message == "Remontando áudio..."
    assert events[-1].message == "Publicando nova revisão..."
    assert all(event.eta_seconds is None for event in events)


def test_ready_model_skips_model_loading_telemetry():
    events = []
    tracker = ProgressTracker(events.append)
    tracker.report("preparing", message="ready")
    tracker.expect_model_loading(False)
    tracker.report("model", message="engine legacy callback")
    tracker.report("generation", 1, 2, "generation")
    assert [event.phase for event in events] == ["preparing", "generation"]
