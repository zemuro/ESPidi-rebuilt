"""Фасад устройства под тестом: консоль + MIDI + типовые действия оператора."""
from __future__ import annotations

import time

from .clock import SteppedClock, RealtimeClock
from .console import Console
from .midi import Midi
from .patterns import Pattern, Song, pattern_path, song_path

APP = dict(arp=0, mel=1, song=2, mon=3, set=4)


class Dut:
    def __init__(self, console: Console, midi: Midi):
        self.c, self.midi = console, midi

    # ------------------------------------------------------------ состояние
    def settle(self, quiet_ms=80, timeout=4.0):
        """Дождаться тишины на MIDI-выходе и очистить буфер захвата."""
        self.midi.wait_quiet(quiet_ms, timeout)
        self.midi.clear()

    def reset(self):
        self.c.handshake()   # после простоя USB-консоль может потерять первые запросы — «прогреваем»
        self.c.reset()
        self.settle()

    def state(self) -> dict:
        return self.c.state()

    # ------------------------------------------------------------ настройка
    def use(self, app: str, **params):
        """Перейти в приложение и выставить параметры по подписям экрана (BPM, MODE, CH, …)."""
        self.c.app(APP[app])
        for k, v in params.items():
            self.c.param(k, v)

    def settings(self, clkin=None, clkout=None, start=None, bright=None):
        """Изменить SETTINGS и вернуться в прежнее приложение."""
        prev = self.state()["app"]
        self.c.app(APP["set"])
        for label, v in (("CLKIN", clkin), ("CLKOUT", clkout), ("START", start), ("BRIGHT", bright)):
            if v is not None:
                self.c.param(label, int(v))
        self.c.app(prev)

    def bpm(self, v: int):
        self.c.param("BPM", v)

    # ------------------------------------------------------------ действия оператора
    def play(self):
        self.c.click("play")

    def rec(self):
        self.c.combo("tap", "play")

    def step_edit(self):
        self.c.combo("tap", "lr")

    def clear(self):
        self.c.click("play", long=True)

    # ------------------------------------------------------------ файлы
    def install_pattern(self, slot0: int, pat: Pattern):
        self.c.fs_put(pattern_path(slot0), pat.to_bytes())

    def install_song(self, slot0: int, song: Song):
        self.c.fs_put(song_path(slot0), song.to_bytes())

    def load_pattern(self, slot0: int):
        assert self.c.load("mel", slot0)

    def load_song(self, slot0: int):
        assert self.c.load("song", slot0)

    # ------------------------------------------------------------ часы
    def stepped(self, **kw) -> SteppedClock:
        return SteppedClock(self.midi, **kw)

    def realtime(self, bpm=120.0) -> RealtimeClock:
        return RealtimeClock(self.midi, bpm)

    def external_clock(self, start_transport=False):
        self.settings(clkin=True, start=start_transport)
