"""C. Корректность MIDI: дубли, зависшие ноты, сквозная передача, монитор."""
import time

import pytest

from esptest import analysis
from esptest.midi import Msg


def retriggers(msgs):
    """NoteOn на уже звучащую ноту без NoteOff между ними."""
    sounding, bad = set(), []
    for m in msgs:
        if m.kind == "note_on":
            key = (m.ch, m.d1)
            if key in sounding:
                bad.append(key)
            sounding.add(key)
        elif m.kind == "note_off":
            sounding.discard((m.ch, m.d1))
        elif m.is_cc(123):
            sounding = {k for k in sounding if k[0] != m.ch}
    return bad


@pytest.mark.tid("C1", "P0")
def test_c1_seq_strum_no_double_send(dut, rec):
    """SEQ, STRUM=ON, REC выключен: нота должна уйти на выход один раз."""
    dut.use("mel", STRUM=1)
    dut.midi.clear()
    dut.midi.note_on(1, 60, 100)
    dut.midi.wait_quiet(60, 1.0)
    dut.midi.note_off(1, 60)
    dut.midi.wait_quiet(60, 1.0)
    msgs = dut.midi.since(0)
    ons = analysis.count(msgs, lambda m: m.is_note_on(1, 60))
    offs = analysis.count(msgs, lambda m: m.is_note_off(1, 60))
    rec("NoteOn / NoteOff на выходе", f"{ons} / {offs}")
    assert (ons, offs) == (1, 1), f"ожидалось 1/1, получено {ons}/{offs}"


@pytest.mark.tid("C2", "P0")
def test_c2_arp_hold_swallows_note_off(dut):
    """ARP, HOLD=ON, арпеджиатор остановлен: NoteOn проходит, NoteOff не должен теряться."""
    dut.use("arp", HOLD=1)
    dut.midi.clear()
    dut.midi.note_on(1, 60, 100)
    dut.midi.wait_quiet(60, 1.0)
    dut.midi.note_off(1, 60)
    dut.midi.wait_quiet(60, 1.0)
    hung = analysis.hanging_notes(dut.midi.since(0))
    assert not hung, f"зависшие ноты на выходе: {sorted(hung)}"


@pytest.mark.tid("C3", "P1")
def test_c3_arp_retrigger_without_note_off(dut, rec):
    """Новая клавиша при играющем арпеджио: NoteOn арпа перезаписывает lastNote без NoteOff."""
    dut.settings(clkin=True)
    dut.use("arp", DIV=2, GATE=100, STRUM=0)
    dut.play()
    dut.midi.clear()
    clk = dut.stepped()
    dut.midi.note_on(1, 60, 100)
    clk.tick(3)
    dut.midi.note_on(1, 64, 100)
    clk.tick(3)
    bad = retriggers(dut.midi.since(0))
    rec("NoteOn поверх звучащей ноты", bad)
    assert not bad, f"NoteOn на звучащую ноту без NoteOff: {bad}"


@pytest.mark.tid("C4", "P1")
@pytest.mark.design
def test_c4_seq_cc123_during_playback(dut, rec):
    """CC123 на всех каналах — решение Евгения для STOP. Но на КАЖДОМ шаге воспроизведения?"""
    from esptest.patterns import straight
    dut.install_pattern(0, straight(16, note=60))
    dut.load_pattern(0)
    dut.settings(clkin=True)
    dut.use("mel")
    dut.settle(100, 2)
    dut.play()
    log = dut.stepped().tick(30)
    cc = log.events(lambda m: m.is_cc(123))
    rec("CC123 за 30 тиков воспроизведения (5 шагов)", len(cc))
    assert not cc, f"{len(cc)} сообщений CC123 во время воспроизведения (по 16 каналам на каждом шаге)"


_PASS = {
    "pitchbend": lambda m: m.pitchbend(1, 8192 + 100),
    "program": lambda m: m.program(1, 5),
    "channel_pressure": lambda m: m.channel_pressure(1, 77),
    "poly_pressure": lambda m: m.poly_pressure(1, 60, 55),
    "sysex": lambda m: m.sysex(0x7D, 1, 2, 3),
}


@pytest.mark.tid("C5", "P1")
@pytest.mark.design
@pytest.mark.parametrize("name", list(_PASS))
def test_c5_passthrough_other_messages(dut, name):
    """ARP остановлен: прозрачность канальных сообщений, не связанных с нотами/CC."""
    dut.midi.clear()
    _PASS[name](dut.midi)
    dut.midi.wait_quiet(80, 1.0)
    out = dut.midi.since(0)
    assert out, f"{name}: на выходе тишина — сообщение отброшено прошивкой"


def _glyph(scr, x, y):
    return tuple(scr.px(x + dx, y + dy) for dy in range(8) for dx in range(6))


@pytest.mark.tid("C6", "P1")
def test_c6_monitor_shows_wrong_channel(dut, rec):
    """MONITOR печатает `channel + 1` от уже 1-based канала. Эталон цифры берём с экрана ARP (параметр CH)."""
    c = dut.c
    ref = {}
    for ch in range(1, 10):
        c.param("CH", ch)
        c.click("lr")                  # курсор в левую колонку, чтобы значение CH не было инвертировано
        time.sleep(0.2)
        ref[ch] = _glyph(c.screen(), 82, 0)
    c.app(3)                           # MONITOR
    bad = []
    for ch in range(1, 9):
        dut.midi.note_on(ch, 60, 100)
        time.sleep(0.25)
        shown = _glyph(c.screen(), 12, 0)
        dut.midi.note_off(ch, 60)
        time.sleep(0.1)
        match = [k for k, g in ref.items() if g == shown]
        if match != [ch]:
            bad.append((ch, match))
    rec("(отправлен канал, показано)", bad)
    assert not bad, f"монитор показывает не тот канал: {bad}"


@pytest.mark.tid("C7", "P2")
def test_c7_note_on_velocity_zero_is_note_off(dut):
    dut.midi.clear()
    dut.midi.note_on(1, 60, 100)
    dut.midi.wait_quiet(60, 1.0)
    dut.midi.note_on(1, 60, 0)
    dut.midi.wait_quiet(60, 1.0)
    assert not analysis.hanging_notes(dut.midi.since(0))


@pytest.mark.tid("C9", "P2")
def test_c9_arp_capacity_8_keys_4_octaves(dut, rec):
    dut.settings(clkin=True)
    dut.use("arp", STRUM=0, OCT=4, DIV=5, GATE=60)
    dut.play()
    dut.midi.clear()
    for n in range(60, 69):            # 9 клавиш, 9-я не должна попасть
        dut.midi.note_on(1, n, 100)
    dut.midi.wait_quiet(60, 1.0)
    dut.midi.clear()
    log = dut.stepped(quiet_ms=15).tick(110)
    notes = {m.d1 for _, m in log.events(lambda m: m.kind == "note_on")}
    rec("различных нот в арпеджио", len(notes))
    assert 68 not in notes and 68 + 12 not in notes, "9-я клавиша не должна участвовать"
    assert len(notes) == 32, f"ожидалось 8 клавиш × 4 октавы = 32 ноты, получено {len(notes)}"


@pytest.mark.tid("C10", "P0")
def test_c10_seq_rec_strum_off_swallows_note_off(dut):
    """SEQ, STRUM=OFF (по умолчанию), REC ON: NoteOn проходит на выход, а NoteOff съедается."""
    dut.use("mel")
    dut.rec()
    assert dut.state()["mel"]["rec"] == 1
    dut.midi.clear()
    dut.midi.note_on(1, 60, 100)
    dut.midi.wait_quiet(60, 1.0)
    dut.midi.note_off(1, 60)
    dut.midi.wait_quiet(60, 1.0)
    hung = analysis.hanging_notes(dut.midi.since(0))
    assert not hung, f"зависшие ноты на выходе: {sorted(hung)}"
