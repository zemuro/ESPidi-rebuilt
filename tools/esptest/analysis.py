"""Анализ захваченных MIDI-потоков и метрик устройства."""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from . import thresholds
from .midi import Msg

# Границы корзин гистограмм прошивки (мкс) — см. test_hooks.cpp
EDGES_MS = [500, 1000, 2000, 5000, 10000, 20000, 50000, 100000]
EDGES_LATE = [100, 500, 1000, 2000, 5000, 10000, 20000]


def clock_times(msgs) -> list[float]:
    return [m.t for m in msgs if m.kind == "clock"]


def intervals_ms(times: list[float]) -> list[float]:
    return [(b - a) * 1000.0 for a, b in zip(times, times[1:])]


@dataclass
class Jitter:
    n: int
    mean: float
    std: float
    min: float
    max: float
    p99: float
    nominal: float
    over: int            # интервалов, превышающих nominal * 1.25
    worst_gap_ms: float

    def __str__(self):
        return (f"n={self.n} mean={self.mean:.3f} σ={self.std:.3f} min={self.min:.2f} "
                f"max={self.max:.2f} p99={self.p99:.2f} (nominal {self.nominal:.3f}) "
                f"выбросов>{self.nominal * 1.25:.1f}мс: {self.over}")


def jitter(times: list[float], bpm: float) -> Jitter:
    nominal = 60000.0 / (bpm * 24)
    iv = intervals_ms(times)
    if len(iv) < 3:
        raise ValueError("слишком мало тактов для статистики")
    srt = sorted(iv)
    p99 = srt[min(len(srt) - 1, int(len(srt) * 0.99))]
    return Jitter(len(iv), statistics.fmean(iv), statistics.pstdev(iv), srt[0], srt[-1], p99, nominal,
                  sum(1 for x in iv if x > nominal * 1.25), srt[-1])


def hanging_notes(msgs) -> set:
    """(канал, нота), у которых после последнего NoteOn не было NoteOff (и не было CC123 на канале)."""
    on = set()
    for m in msgs:
        if m.kind == "note_on":
            on.add((m.ch, m.d1))
        elif m.kind == "note_off":
            on.discard((m.ch, m.d1))
        elif m.is_cc(123):
            on = {x for x in on if x[0] != m.ch}
    return on


def bytes_on_wire(msgs) -> int:
    return sum(len(m.data) for m in msgs)


def wire_time_ms(msgs) -> float:
    """Время передачи сообщений по MIDI 31250 бод (10 бит на байт)."""
    return bytes_on_wire(msgs) * 10 / 31250.0 * 1000.0


def count(msgs, pred) -> int:
    return sum(1 for m in msgs if pred(m))


def hist_pct_over(hist: dict, edges: list[int], threshold_us: int) -> int:
    """Сколько событий гистограммы прошивки попало в корзины с нижней границей >= threshold_us."""
    total = 0
    lows = [0] + list(edges)
    for low, n in zip(lows, hist["b"]):
        if low >= threshold_us:
            total += n
    return total


def fmt_hist(hist: dict, edges: list[int]) -> str:
    labels = [f"<{e/1000:g}мс" for e in edges] + [f"≥{edges[-1]/1000:g}мс"]
    return " ".join(f"{l}:{n}" for l, n in zip(labels, hist["b"]) if n)


def pc_limit_ms(noise: Jitter, bpm=120) -> float:
    """Предельный допустимый интервал Clock на ПК: максимум из 1,25·номинала и шумового пола + запас."""
    nominal = 60000.0 / (bpm * 24)
    return max(nominal * 1.25, noise.max + thresholds.PC_JITTER_MARGIN_MS)
