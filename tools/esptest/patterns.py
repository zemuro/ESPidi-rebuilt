"""Построители бинарных файлов паттернов (MSEQ v2) и песен (SONG v1) — формат из seq_mel.cpp / seq_song.cpp."""
from __future__ import annotations

import struct

MAX_STEPS, MAX_POLY, MAX_CC = 64, 6, 3
HDR = 5

# MelSeqParams: bpm, channel, mode, length, gate, page, reRec, strum, follow, swing, randomness, probability
PARAM_FIELDS = ("bpm", "channel", "mode", "length", "gate", "page", "reRec", "strum", "follow",
                "swing", "randomness", "probability")


class Pattern:
    """Паттерн секвенсора. Параметры по умолчанию совпадают с тем, что прошивка пишет при SAVE
    на «чистом» устройстве (bpm=0, channel=0 в файле) — чтобы работал побайтовый round-trip."""

    def __init__(self, length=16, gate=80, mode=0, channel=1):
        self.params = dict(bpm=0, channel=0, mode=mode, length=length, gate=gate, page=1, reRec=1,
                           strum=0, follow=1, swing=0, randomness=0, probability=0)
        self.default_ch = channel
        self.notes = [[-1] * MAX_POLY for _ in range(MAX_STEPS)]
        self.vel = [[100] * MAX_POLY for _ in range(MAX_STEPS)]
        self.ncount = [0] * MAX_STEPS
        self.cc_n = [[0] * MAX_CC for _ in range(MAX_STEPS)]
        self.cc_v = [[0] * MAX_CC for _ in range(MAX_STEPS)]
        self.cc_count = [0] * MAX_STEPS
        self.tie = [0] * MAX_STEPS
        self.transpose = [0] * MAX_STEPS
        self.channels = [[1] * MAX_POLY for _ in range(MAX_STEPS)]
        self.length_ticks = [[0] * MAX_POLY for _ in range(MAX_STEPS)]

    def step(self, i, notes=(), cc=(), tie=False, transpose=0, vel=100, channel=None):
        assert len(notes) <= MAX_POLY and len(cc) <= MAX_CC
        for k, n in enumerate(notes):
            self.notes[i][k] = n
            self.vel[i][k] = vel
            self.channels[i][k] = channel or self.default_ch
        self.ncount[i] = len(notes)
        for k, (num, val) in enumerate(cc):
            self.cc_n[i][k], self.cc_v[i][k] = num, val
        self.cc_count[i] = len(cc)
        self.tie[i] = int(tie)
        self.transpose[i] = transpose
        return self

    def to_bytes(self) -> bytes:
        p = self.params
        out = b"MSEQ" + bytes([2])
        out += bytes(p[f] for f in PARAM_FIELDS)
        out += b"".join(struct.pack("6b", *r) for r in self.notes)
        out += b"".join(bytes(r) for r in self.vel)
        out += bytes(self.ncount)
        out += b"".join(bytes(r) for r in self.cc_n)
        out += b"".join(bytes(r) for r in self.cc_v)
        out += bytes(self.cc_count)
        out += bytes(self.tie)
        out += struct.pack("64b", *self.transpose)
        out += b"".join(bytes(r) for r in self.channels)
        out += b"".join(bytes(r) for r in self.length_ticks)
        return out

    @staticmethod
    def layout():
        """[(имя поля, смещение, размер)] для сообщений о первом расхождении файлов."""
        spec = [("magic+version", 5), ("params", 12), ("notes", 384), ("velocities", 384), ("noteCount", 64),
                ("ccNumber", 192), ("ccValue", 192), ("ccCount", 64), ("tie", 64), ("transpose", 64),
                ("channels", 384), ("lengthTicks", 384)]
        off, out = 0, []
        for name, size in spec:
            out.append((name, off, size))
            off += size
        return out

    @staticmethod
    def locate(offset: int) -> str:
        for name, off, size in Pattern.layout():
            if off <= offset < off + size:
                rel = offset - off
                return f"{name}[+{rel}]"
        return f"offset {offset}"


def pattern_path(slot0: int) -> str:
    return f"/patterns/pat_{slot0:02d}.bin"


def song_path(slot0: int) -> str:
    return f"/songs/song_{slot0:02d}.bin"


def straight(length=16, note=60, gate=80, every=1) -> Pattern:
    """Нота на каждом `every`-м шаге."""
    p = Pattern(length=length, gate=gate)
    for i in range(0, length, every):
        p.step(i, [note])
    return p


class Song:
    def __init__(self, length=2):
        self.length = length
        self.steps = [dict(slot=0, tr=0, div=2, pause=16, mute=0) for _ in range(64)]

    def step(self, i, slot=0, tr=0, div=2, pause=16, mute=0):
        self.steps[i] = dict(slot=slot, tr=tr, div=div, pause=pause, mute=mute)
        return self

    def to_bytes(self) -> bytes:
        out = b"SONG" + bytes([1, self.length])
        for s in self.steps:
            out += struct.pack("BbBBB", s["slot"], s["tr"], s["div"], s["pause"], s["mute"])
        return out
