"""Дымовые тесты стенда: консоль, виртуальные кнопки, MIDI-петля. Если они не проходят —
остальные результаты недостоверны."""
import time

import pytest

from esptest import analysis

pytestmark = pytest.mark.tid("S", "P0")


def test_console_state(dut):
    st = dut.state()
    assert st["app"] == 0 and st["bpm"] == 120
    assert st["heap"] > 50_000


def test_virtual_buttons_drive_real_ui_code(dut):
    """L/R переключает колонку через реальный inputs_pollButtons/ui_handleButton."""
    c = dut.c
    assert dut.state()["col"] == 0
    c.click("lr")
    assert dut.state()["col"] == 1
    c.click("lr")
    assert dut.state()["col"] == 0


def test_encoder_moves_cursor(dut):
    dut.c.enc(1)
    assert dut.state()["row"] == 1


def test_long_press_encoder_opens_menu(dut):
    dut.c.click("enc", long=True)
    assert dut.state()["ui"] == 2          # UI_MENU


def test_param_setter_uses_ui_path(dut):
    assert dut.c.param("BPM", 133) == 133
    assert dut.state()["bpm"] == 133


def test_midi_loopback_passthrough(dut):
    """Нота с ПК → MIDI IN ESPidi → (ARP остановлен: прозрачный проход) → MIDI OUT → ПК."""
    dut.midi.note_on(1, 60, 100)
    dut.midi.wait_quiet(60, 1.0)
    msgs = dut.midi.since(0)
    assert any(m.is_note_on(1, 60) for m in msgs), f"нота не вернулась: {msgs}"
    dut.midi.note_off(1, 60)


def test_screen_dump_not_blank(dut):
    scr = dut.c.screen()
    assert scr.lit() > 50


def test_clock_out_reaches_pc(dut):
    dut.settings(clkout=True)
    msgs = dut.midi.capture(1.5)
    n = len(analysis.clock_times(msgs))
    assert 40 <= n <= 80, f"ожидалось ≈48 тактов/с·1,5 с = 72, получено {n}"
