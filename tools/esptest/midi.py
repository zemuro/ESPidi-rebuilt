"""MIDI-ввод/вывод для тестов: захват с метками времени и отправка сырых байтов.

Входной порт ПК = выход ESPidi (MIDI OUT); выходной порт ПК = вход ESPidi (MIDI IN).
Метка времени — `time.perf_counter()` в момент вызова callback драйвера. На Windows
(MME) это даёт точность порядка 1–2 мс: достаточно, чтобы ловить события ≥ 5 мс
(блокировки по 12/15/123 мс), но не для суб-миллисекундного джиттера.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import rtmidi

CLOCK, START, CONT, STOP, SENSE = 0xF8, 0xFA, 0xFB, 0xFC, 0xFE


@dataclass(frozen=True)
class Msg:
    t: float            # секунды, perf_counter
    data: tuple

    @property
    def status(self):
        return self.data[0]

    @property
    def kind(self) -> str:
        s = self.data[0]
        if s >= 0xF0:
            return {CLOCK: "clock", START: "start", CONT: "continue", STOP: "stop",
                    SENSE: "sense", 0xF0: "sysex"}.get(s, f"sys{s:02x}")
        k = s & 0xF0
        if k == 0x90 and self.data[2] == 0:
            return "note_off"
        return {0x80: "note_off", 0x90: "note_on", 0xA0: "poly_at", 0xB0: "cc",
                0xC0: "program", 0xD0: "chan_at", 0xE0: "pitchbend"}[k]

    @property
    def ch(self) -> int:
        """Канал 1–16 (для канальных сообщений)."""
        return (self.data[0] & 0x0F) + 1

    @property
    def d1(self):
        return self.data[1] if len(self.data) > 1 else None

    @property
    def d2(self):
        return self.data[2] if len(self.data) > 2 else None

    def is_note_on(self, ch=None, note=None):
        return self.kind == "note_on" and (ch is None or self.ch == ch) and (note is None or self.d1 == note)

    def is_note_off(self, ch=None, note=None):
        return self.kind == "note_off" and (ch is None or self.ch == ch) and (note is None or self.d1 == note)

    def is_cc(self, number=None, ch=None):
        return self.kind == "cc" and (number is None or self.d1 == number) and (ch is None or self.ch == ch)

    def __repr__(self):
        return f"<{self.t:.4f} {self.kind} {list(self.data)}>"


def list_ports():
    ins = rtmidi.MidiIn()
    outs = rtmidi.MidiOut()
    return ins.get_ports(), outs.get_ports()


_BLACKLIST = ("bome", "tevirtual", "microsoft gs", "midi through", "loopmidi")


def find_ports(in_hint: str | None = None, out_hint: str | None = None):
    """Подобрать порты. Подсказки — подстрока имени (без учёта регистра). Если подсказок нет,
    берём единственный порт вне чёрного списка (виртуальные драйверы Bome/teVirtualMIDI и т. п.)."""
    ins, outs = list_ports()

    def pick(names, hint):
        if hint:
            for i, n in enumerate(names):
                if hint.lower() in n.lower():
                    return i
            return None
        good = [i for i, n in enumerate(names) if not any(b in n.lower() for b in _BLACKLIST)]
        return good[0] if len(good) == 1 else None

    return pick(ins, in_hint), pick(outs, out_hint), ins, outs


class MidiPortHang(RuntimeError):
    pass


def probe_ports(in_index: int, out_index: int, timeout: float = 10.0):
    """Драйверы дешёвых USB-MIDI кабелей после аварийно завершённого процесса могут навсегда
    зависнуть в midiInOpen/midiOutOpen, причём python-rtmidi держит GIL — тайм-аут внутри
    процесса не спасает. Поэтому пробуем открыть порты в подпроцессе."""
    import subprocess, sys
    code = ("import rtmidi;i=rtmidi.MidiIn();i.open_port(%d);o=rtmidi.MidiOut();o.open_port(%d);"
            "i.close_port();o.close_port()" % (in_index, out_index))
    try:
        subprocess.run([sys.executable, "-c", code], timeout=timeout, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.TimeoutExpired:
        raise MidiPortHang(f"драйвер MIDI не открыл порты за {timeout:.0f} с — переподключите "
                           "USB-MIDI кабель (порт остался занят зависшим процессом)")


class Midi:
    def __init__(self, in_index: int, out_index: int):
        self._in = rtmidi.MidiIn()
        self._in.ignore_types(sysex=False, timing=False, active_sense=False)
        self._in.open_port(in_index)
        self._out = rtmidi.MidiOut()
        self._out.open_port(out_index)
        self._lock = threading.Lock()
        self._msgs: list[Msg] = []
        self._in.set_callback(self._cb)

    def _cb(self, event, _data=None):
        msg, _delta = event
        m = Msg(time.perf_counter(), tuple(msg))
        with self._lock:
            self._msgs.append(m)

    def close(self):
        try:
            self._in.cancel_callback()
            self._in.close_port()
            self._out.close_port()
        except Exception:
            pass

    # ------------------------------------------------------------ захват
    def mark(self) -> int:
        with self._lock:
            return len(self._msgs)

    def since(self, mark: int = 0) -> list[Msg]:
        with self._lock:
            return list(self._msgs[mark:])

    def clear(self):
        with self._lock:
            self._msgs.clear()

    def wait_quiet(self, quiet_ms=30.0, timeout=2.0) -> bool:
        """Ждать, пока MIDI-вход молчит `quiet_ms` подряд. True — тишина наступила."""
        end = time.perf_counter() + timeout
        last_n, last_t = self.mark(), time.perf_counter()
        while time.perf_counter() < end:
            time.sleep(0.002)
            n = self.mark()
            now = time.perf_counter()
            if n != last_n:
                last_n, last_t = n, now
            elif (now - last_t) * 1000 >= quiet_ms:
                return True
        return False

    def capture(self, seconds: float) -> list[Msg]:
        m = self.mark()
        time.sleep(seconds)
        return self.since(m)

    # ------------------------------------------------------------ отправка
    def send(self, *data):
        self._out.send_message(list(data))

    def note_on(self, ch, note, vel=100):
        self.send(0x90 | (ch - 1), note, vel)

    def note_off(self, ch, note, vel=0):
        self.send(0x80 | (ch - 1), note, vel)

    def cc(self, ch, number, value):
        self.send(0xB0 | (ch - 1), number, value)

    def program(self, ch, p):
        self.send(0xC0 | (ch - 1), p)

    def pitchbend(self, ch, value14=8192):
        self.send(0xE0 | (ch - 1), value14 & 0x7F, value14 >> 7)

    def channel_pressure(self, ch, v):
        self.send(0xD0 | (ch - 1), v)

    def poly_pressure(self, ch, note, v):
        self.send(0xA0 | (ch - 1), note, v)

    def sysex(self, *payload):
        self.send(0xF0, *payload, 0xF7)

    def clock(self):
        self.send(CLOCK)

    def start(self):
        self.send(START)

    def stop(self):
        self.send(STOP)

    def cont(self):
        self.send(CONT)

    def sense(self):
        self.send(SENSE)

    def all_notes_off_input(self, ch=None):
        for c in ([ch] if ch else range(1, 17)):
            self.cc(c, 123, 0)
