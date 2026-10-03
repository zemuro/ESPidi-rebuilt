"""F — ввод/UI, G — транспорт и внешняя синхронизация, H — устойчивость."""
import time

import pytest

from esptest import analysis


# ------------------------------------------------------------------ F

@pytest.mark.tid("F2", "P1")
@pytest.mark.skip(reason="решение автора: шаг за итерацию цикла — так задумано (Евгений, 2026-10-03)")
def test_f2_step_edit_fast_turn(dut, rec):
    """Быстрый поворот (в основном цикле накопилось 10 щелчков) должен сдвинуть шаг на 10, а не на 1."""
    dut.use("mel")
    dut.step_edit()
    st = dut.state()["mel"]
    assert st["stepedit"]
    start = st["edit"]
    dut.c.enc(10)
    time.sleep(0.2)
    moved = (dut.state()["mel"]["edit"] - start) % 16
    rec("сдвиг редактируемого шага после 10 щелчков", moved)
    assert moved == 10, f"шаг сместился на {moved}: delta сводится к знаку"


# ------------------------------------------------------------------ G

@pytest.mark.tid("G1", "P1")
def test_g1_external_clock_bpm_display(dut, rec):
    dut.settings(clkin=True)
    st = dut.state()["clk"]
    assert st["ext"] and not st["alive"] and st["disp"] == 0
    rt = dut.realtime(130)
    rt.start()
    time.sleep(6.0)                    # сглаживание 0,85/0,15 по окнам 0,4 с
    st = dut.state()["clk"]
    rt.stop()
    rec("отображаемый BPM при Clock 130", st["disp"])
    assert st["alive"] and abs(st["disp"] - 130) <= 3, f"показано {st['disp']}"
    time.sleep(0.8)
    st = dut.state()["clk"]
    assert not st["alive"] and st["disp"] == 0, "потеря Clock не обнаружена (>0,5 с)"


@pytest.mark.tid("G2", "P1")
def test_g2_remote_transport_controls_app(dut):
    dut.settings(clkin=True, start=True)
    dut.use("arp")
    dut.midi.start()
    time.sleep(0.3)
    assert dut.state()["arp"]["en"], "Start не запустил приложение"
    dut.midi.stop()
    time.sleep(0.3)
    assert not dut.state()["arp"]["en"], "Stop не остановил приложение"
    dut.midi.cont()
    time.sleep(0.3)
    assert dut.state()["arp"]["en"], "Continue не запустил приложение"


@pytest.mark.tid("G2", "P1")
def test_g2b_local_play_sends_start_stop(dut):
    dut.settings(start=True)
    dut.use("arp")
    dut.midi.clear()
    dut.play()
    dut.midi.wait_quiet(60, 1.0)
    dut.play()
    dut.midi.wait_quiet(60, 1.0)
    kinds = [m.kind for m in dut.midi.since(0) if m.kind in ("start", "stop")]
    assert kinds == ["start", "stop"], f"на выходе {kinds}"


@pytest.mark.tid("G3", "P1")
def test_g3_clock_and_transport_forwarded(dut, rec):
    dut.settings(clkin=True, clkout=True)
    dut.midi.clear()
    rt = dut.realtime(120)
    rt.start(send_start=True)
    time.sleep(1.5)
    dut.midi.cont()
    time.sleep(0.2)
    rt.stop(send_stop=True)
    time.sleep(0.4)
    out = dut.midi.since(0)
    kinds = [m.kind for m in out if m.kind in ("start", "continue", "stop")]
    n_out, n_in = len(analysis.clock_times(out)), len(rt.sent)
    rec("Clock отправлено / получено; транспорт", f"{n_in} / {n_out}; {kinds}")
    assert kinds == ["start", "continue", "stop"], f"транспорт на выходе: {kinds}"
    assert abs(n_out - n_in) <= 2, f"Clock потерян/задвоен: {n_in} → {n_out}"


@pytest.mark.tid("G4", "P2")
@pytest.mark.design
def test_g4_clock_ignored_when_clkin_off(dut, rec):
    dut.settings(clkin=False, clkout=False)
    dut.midi.clear()
    rt = dut.realtime(120)
    rt.start(send_start=True)
    time.sleep(1.0)
    rt.stop(send_stop=True)
    time.sleep(0.3)
    out = [m for m in dut.midi.since(0) if m.kind in ("clock", "start", "stop")]
    rec("реалтайм-сообщений на выходе при CLKIN=OFF, CLKOUT=OFF", len(out))
    assert not out, f"прошивка пересылает {len(out)} реалтайм-сообщений при выключенных CLKIN/CLKOUT"


# ------------------------------------------------------------------ H

@pytest.mark.tid("H1", "P1")
def test_h1_cc_flood_with_external_clock(dut, rec, run_s):
    """500 CC/с + внешний Clock 120: ни потерь CC, ни провалов обслуживания."""
    dut.settings(clkin=True)
    rt = dut.realtime(120)
    dut.c.stats_reset()
    dut.midi.clear()
    rt.start()
    sent, t0, secs = 0, time.perf_counter(), min(run_s, 10)
    period = 1 / 500
    nxt = t0
    while time.perf_counter() - t0 < secs:
        now = time.perf_counter()
        if now >= nxt:
            dut.midi.cc(2, 1 + sent % 100, sent % 128)
            sent += 1
            nxt += period
        else:
            time.sleep(0.0005)
    rt.stop()
    dut.midi.wait_quiet(100, 2.0)
    got = analysis.count(dut.midi.since(0), lambda m: m.is_cc(ch=2) and m.d1 != 123)
    st = dut.c.stats()
    rec("CC отправлено / получено; gap max, мкс; loop max, мкс", f"{sent} / {got}; {st['gap']['max']}; {st['loop']['max']}")
    assert got == sent, f"потеряно {sent - got} из {sent} CC"


@pytest.mark.tid("H4", "P2")
def test_h4_active_sensing_ignored(dut):
    before = dut.state()
    dut.midi.clear()
    for _ in range(10):
        dut.midi.sense()
        time.sleep(0.3)
    assert not dut.midi.since(0), "Active Sensing вызвал вывод"
    after = dut.state()
    assert before["app"] == after["app"] and before["clk"]["alive"] == after["clk"]["alive"]
