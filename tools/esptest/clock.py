"""Источники MIDI Clock для тестов.

* RealtimeClock — поток с жёстким расписанием (для измерения реального времени).
* SteppedClock — детерминированный режим: один Clock → ждём, пока ESPidi «договорит»
  (тишина на входе) → следующий. Результат не зависит от джиттера ПК, события
  привязываются к номеру тика. Скорость ≈ 25–40 мс на тик.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .midi import Midi, Msg

try:  # Windows: разрешение таймера 1 мс
    import ctypes
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:  # pragma: no cover
    pass


class RealtimeClock:
    def __init__(self, midi: Midi, bpm: float = 120.0):
        self.midi, self.bpm = midi, bpm
        self.sent: list[float] = []     # perf_counter отправки каждого тика
        self._run = threading.Event()
        self._th: threading.Thread | None = None

    def _loop(self):
        interval = 60.0 / (self.bpm * 24)
        nxt = time.perf_counter()
        while self._run.is_set():
            now = time.perf_counter()
            if now < nxt:
                if nxt - now > 0.002:
                    time.sleep(nxt - now - 0.0015)
                continue
            self.midi.clock()
            self.sent.append(now)
            nxt += interval

    def start(self, send_start=False):
        if send_start:
            self.midi.start()
        self._run.set()
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    def stop(self, send_stop=False):
        self._run.clear()
        if self._th:
            self._th.join(1)
        if send_stop:
            self.midi.stop()


@dataclass
class TickLog:
    """Сообщения ESPidi, пришедшие после тика №i (до следующего)."""
    per_tick: list = field(default_factory=list)   # list[list[Msg]]
    marker_msgs: list = field(default_factory=list)  # до первого тика (после Start и т. п.)

    def flat(self):
        return [(i, m) for i, ms in enumerate(self.per_tick) for m in ms]

    def events(self, pred):
        """[(номер_тика, msg)] для сообщений, удовлетворяющих pred."""
        return [(i, m) for i, m in self.flat() if pred(m)]


class SteppedClock:
    # max_wait < 0,5 с: прошивка считает Clock потерянным после 0,5 с тишины и останавливается
    def __init__(self, midi: Midi, quiet_ms: float = 28.0, max_wait: float = 0.35):
        self.midi, self.quiet_ms, self.max_wait = midi, quiet_ms, max_wait

    def tick(self, n: int = 1) -> TickLog:
        log = TickLog()
        for _ in range(n):
            m0 = self.midi.mark()
            self.midi.clock()
            time.sleep(0.004)
            self.midi.wait_quiet(self.quiet_ms, self.max_wait)
            log.per_tick.append([m for m in self.midi.since(m0) if m.kind != "clock"])
        return log

    def start(self) -> list[Msg]:
        m0 = self.midi.mark()
        self.midi.start()
        self.midi.wait_quiet(self.quiet_ms, self.max_wait)
        return self.midi.since(m0)

    def stop(self) -> list[Msg]:
        m0 = self.midi.mark()
        self.midi.stop()
        self.midi.wait_quiet(self.quiet_ms, self.max_wait)
        return self.midi.since(m0)
