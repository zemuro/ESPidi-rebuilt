"""Проверки самой инфраструктуры — без устройства."""
import json
import struct
import threading

import pytest

from esptest import analysis
from esptest.console import Console, DeviceError, Screen
from esptest.midi import Msg
from esptest.patterns import Pattern, Song, straight

pytestmark = pytest.mark.unit


def M(t, *data):
    return Msg(t, tuple(data))


# ---------------------------------------------------------------- Msg / анализ

def test_msg_kinds():
    assert M(0, 0x90, 60, 100).kind == "note_on"
    assert M(0, 0x90, 60, 0).kind == "note_off"
    assert M(0, 0x8F, 60, 0).ch == 16
    assert M(0, 0xB0, 123, 0).is_cc(123, 1)
    assert M(0, 0xF8).kind == "clock"


def test_hanging_notes_and_cc123():
    msgs = [M(0, 0x90, 60, 90), M(1, 0x90, 64, 90), M(2, 0x80, 60, 0), M(3, 0xB0, 123, 0)]
    assert analysis.hanging_notes(msgs) == set()
    assert analysis.hanging_notes(msgs[:3]) == {(1, 64)}


def test_jitter_detects_outlier():
    nominal = 60.0 / (120 * 24)
    t = [i * nominal for i in range(200)]
    t[100:] = [x + 0.012 for x in t[100:]]          # провал 12 мс
    j = analysis.jitter(t, 120)
    assert j.over == 1 and 32 < j.max < 34


def test_wire_time():
    msgs = [M(0, 0x80, i, 0) for i in range(128)] + [M(0, 0xB0, 123, 0)]
    assert analysis.bytes_on_wire(msgs) == 387
    assert 123 < analysis.wire_time_ms(msgs) < 125


# ---------------------------------------------------------------- паттерны

def test_pattern_size_and_layout():
    b = straight(16).to_bytes()
    assert len(b) == 2193 and b[:5] == b"MSEQ\x02"
    assert b[5 + 3] == 16                                  # params.length
    assert Pattern.locate(5 + 12 + 5) == "notes[+5]"
    # шаг 0: нота 60, noteCount 1
    notes_off = 5 + 12
    assert struct.unpack_from("6b", b, notes_off)[0] == 60
    assert b[notes_off + 384 + 384] == 1


def test_song_size():
    b = Song(length=4).step(0, slot=2, tr=-3).to_bytes()
    assert len(b) == 6 + 64 * 5 and b[:4] == b"SONG" and b[5] == 4
    assert struct.unpack_from("BbBBB", b, 6) == (2, -3, 2, 16, 0)


# ---------------------------------------------------------------- Screen

def test_screen_pixels():
    raw = bytearray(512)
    raw[5 + 128] = 0b00000100          # x=5, страница 1, бит 2 → y=10
    s = Screen(bytes(raw))
    assert s.px(5, 10) and not s.px(5, 9) and s.lit() == 1


# ---------------------------------------------------------------- протокол консоли на фиктивном порту

class FakeSerial:
    """Имитация устройства: отвечает на `<id> <cmd> ...` как test_hooks.cpp."""
    def __init__(self):
        self.out = b""
        self.cv = threading.Condition()
        self.sent = []

    def write(self, data: bytes):
        for line in data.decode().splitlines():
            self.sent.append(line)
            cid, cmd, *args = line.split(" ")
            if cmd == "ping":
                resp = {"t": 1, "fw": "test"}
            elif cmd == "fail":
                resp = {"err": "boom"}
            elif cmd == "fs" and args[0] == "get":
                resp = {"size": 4, "hex": "deadbeef"}
            else:
                resp = {"ok": 1, "args": args}
            with self.cv:
                self.out += (f"noise before\n@{cid} {json.dumps(resp)}\n").encode()
                self.cv.notify_all()

    def read(self, n):
        with self.cv:
            if not self.out:
                self.cv.wait(0.05)
            chunk, self.out = self.out[:n], self.out[n:]
            return chunk

    def close(self):
        pass


@pytest.fixture
def con():
    c = Console("FAKE")
    c._ser = FakeSerial()
    c._stop = False
    c._reader = threading.Thread(target=c._read_loop, daemon=True)
    c._reader.start()
    yield c
    c._stop = True


def test_console_roundtrip(con):
    assert con.ping()["fw"] == "test"
    assert con.cmd("param", "BPM", 133)["args"] == ["BPM", "133"]
    assert "noise before" in con.log


def test_console_error_and_timeout(con):
    with pytest.raises(DeviceError, match="boom"):
        con.cmd("fail")
    con._ser.write = lambda data: None
    with pytest.raises(DeviceError, match="нет ответа"):
        con.cmd("ping", timeout=0.2)


def test_console_fs_get(con):
    assert con.fs_get("/patterns/pat_00.bin") == bytes.fromhex("deadbeef")
