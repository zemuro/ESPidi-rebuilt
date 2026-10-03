"""D. Логика секвенсора и песни. Время измеряется в ТИКАХ: Clock задаёт ПК (SteppedClock),
поэтому результаты не зависят от джиттера USB/Windows."""
import time

import pytest

from esptest import analysis
from esptest.patterns import Pattern, Song, straight, pattern_path


def _first(log, pred):
    ev = log.events(pred)
    return ev[0][0] if ev else None


@pytest.mark.tid("D1", "P1")
def test_d1_record_lands_on_sounding_step(dut, rec):
    """REC под внешний Clock: нота, сыгранная, пока звучит шаг 0, должна лечь в шаг 0."""
    dut.settings(clkin=True)
    dut.use("mel")
    dut.play()
    dut.rec()
    clk = dut.stepped()
    clk.tick(3)                          # шаг 0 звучит с первого такта после PLAY (такты 0–5)
    dut.midi.note_on(1, 60, 100)
    dut.midi.wait_quiet(60, 1.0)
    dut.midi.note_off(1, 60)
    steps = dut.c.seqdump(0, 3)
    rec("ноты в шагах 0/1/2", [s["n"] for s in steps])
    assert steps[0]["n"] == [60], f"нота записана в шаг {[i for i, s in enumerate(steps) if s['n']]} вместо 0"


@pytest.mark.tid("D3", "P1")
@pytest.mark.parametrize("ties,expected", [(1, 9), (2, 15)])
def test_d3_tie_extends_note_by_one_step_each(dut, rec, ties, expected):
    """Нота с Tie: длина = GATE шага (3 тика при 80) + 6 тиков на каждый Tie."""
    p = Pattern(length=8)
    p.step(0, [60], tie=True)
    for i in range(1, ties):
        p.step(i, [], tie=True)
    dut.install_pattern(0, p)
    dut.load_pattern(0)
    dut.settings(clkin=True)
    dut.use("mel")
    dut.settle(100, 2)
    dut.play()
    log = dut.stepped(quiet_ms=15).tick(expected + 12)
    on = _first(log, lambda m: m.is_note_on(1, 60))
    off = _first(log, lambda m: m.is_note_off(1, 60))
    rec("тик NoteOn / NoteOff", (on, off))
    assert on is not None and off is not None, "нет NoteOn/NoteOff"
    assert off - on == expected, f"длина ноты {off - on} тиков, ожидалось {expected}"


@pytest.mark.tid("D4", "P1")
def test_d4_shrink_length_while_playing_pend(dut, rec):
    """PEND: LENGTH 16→4, когда playhead на шаге ≥10. Playhead должен сразу попасть в границы."""
    dut.settings(clkin=True)
    dut.use("mel", MODE=2)
    dut.play()
    clk = dut.stepped(quiet_ms=10)
    for _ in range(40):
        clk.tick(6)
        if dut.state()["mel"]["step"] >= 10:
            break
    assert dut.state()["mel"]["step"] >= 10
    dut.c.param("LENGTH", 4)
    clk.tick(6)
    step = dut.state()["mel"]["step"]
    rec("шаг после уменьшения LENGTH до 4", step)
    assert step < 4, f"playhead на шаге {step} при LENGTH=4"


@pytest.mark.tid("D5", "P1")
def test_d5_record_limits_in_step_edit(dut, rec):
    """В STEP EDIT запись идёт в выбранный шаг без Clock: 6 нот и 3 CC на шаг, старейшие вытесняются."""
    dut.use("mel")
    dut.step_edit()
    dut.rec()
    st = dut.state()["mel"]
    assert st["rec"] == 1 and st["stepedit"], f"режим записи не включился: {st}"
    for n in range(60, 67):              # 7 нот одним аккордом
        dut.midi.note_on(1, n, 100)
    for cc in (1, 2, 3, 4):
        dut.midi.cc(1, cc, 10 * cc)
    time.sleep(0.3)
    for n in range(60, 67):
        dut.midi.note_off(1, n)
    s0 = dut.c.seqdump(0, 1)[0]
    rec("шаг 0", s0)
    assert s0["n"] == list(range(61, 67)), f"ноты шага: {s0['n']}"
    assert [c[0] for c in s0["cc"]] == [2, 3, 4], f"CC шага: {s0['cc']}"


@pytest.mark.tid("D6", "P0")
def test_d6_pattern_file_roundtrip(dut, rec):
    """Файл → загрузка → SAVE → тот же файл (побайтово)."""
    p = Pattern(length=12)
    p.step(0, [60, 64, 67], cc=[(74, 100), (1, 20)])
    p.step(1, [62], tie=True, transpose=5)
    p.step(2, [], tie=True)
    p.step(5, [48], transpose=-12, vel=77)
    p.step(11, [72, 76], cc=[(7, 127)])
    dut.install_pattern(4, p)
    dut.load_pattern(4)
    dut.use("mel")
    dut.c.param("SAVE")
    after = dut.c.fs_get(pattern_path(4))
    orig = p.to_bytes()
    rec("размер файла (исходный / после SAVE)", f"{len(orig)} / {len(after)}")
    if after != orig:
        i = next((k for k in range(min(len(orig), len(after))) if orig[k] != after[k]), min(len(orig), len(after)))
        pytest.fail(f"файл изменился: первое расхождение на байте {i} ({Pattern.locate(i)})")


@pytest.mark.tid("D7", "P1")
@pytest.mark.design
def test_d7_unsaved_edits_survive_pattern_switch(dut):
    """Правки без SAVE при смене PTRN теряются молча."""
    dut.use("mel")
    dut.step_edit()
    dut.rec()
    dut.midi.note_on(1, 61, 100)
    time.sleep(0.2)
    dut.midi.note_off(1, 61)
    assert dut.c.seqdump(0, 1)[0]["n"] == [61]
    assert dut.state()["mel"]["dirty"]
    dut.c.param("PTRN", 2)
    dut.c.param("PTRN", 1)
    n = dut.c.seqdump(0, 1)[0]["n"]
    assert n == [61], f"несохранённая правка потеряна без предупреждения (шаг 0 = {n})"


@pytest.mark.tid("D8", "P1")
def test_d8_clear_keeps_pattern_slot(dut, rec):
    dut.install_pattern(4, straight(16))
    dut.load_pattern(4)
    dut.use("mel")
    assert dut.state()["mel"]["ptrn"] == 4
    dut.clear()
    ptrn = dut.state()["mel"]["ptrn"]
    rec("внутренний слот после CLEAR (экран показывает PTRN 5)", ptrn)
    assert ptrn == 4, f"после CLEAR слот сброшен в {ptrn}: после перезагрузки загрузится не тот паттерн"


@pytest.mark.tid("D10", "P0")
def test_d10_song_rnd_cycle_off_stops_early(dut, rec):
    """SONG: RND + CYCLE=OFF. Песня из 8 шагов не должна останавливаться, не отыграв 8 шагов."""
    dut.install_pattern(0, Pattern(length=1).step(0, [60]))
    s = Song(length=8)
    for i in range(8):
        s.step(i, slot=1, div=5)          # 1/32 = 3 тика на шаг
    dut.install_song(0, s)
    dut.load_song(0)
    dut.settings(clkin=True)
    dut.use("song", MODE=3, CYCLE=0)
    dut.settle(100, 2)
    clk = dut.stepped(quiet_ms=12)
    plays = []
    for attempt in range(8):
        dut.play()
        log = clk.tick(34)
        n = len(log.events(lambda m: m.is_note_on(1, 60)))
        stopped = not dut.state()["song"]["en"]
        plays.append((n, stopped))
        if stopped and n < 8:
            break
        if not stopped:
            dut.play()                      # остановить, чтобы следующий заход начался с нуля
        dut.settle(60, 2)
    rec("(сыграно шагов, остановилась) по попыткам", plays)
    early = [p for p in plays if p[1] and p[0] < 8]
    assert not early, f"песня остановилась раньше конца ({early[0][0]} из 8 шагов): шаг 0 выпал случайно"


def _song_step_duration(dut, mute):
    """Длительность шага песни по MIDI-выходу, без опроса консоли во время игры (иначе задержки
    консоли выглядят для прошивки как потеря Clock): шаг 1 — паттерн из 8 шагов (с MUTE или без),
    шаг 2 — паттерн с нотой 62. Тик первой ноты 62 = длительность шага 1 + сдвиг первого импульса."""
    dut.install_pattern(0, straight(8, note=60))
    dut.install_pattern(1, Pattern(length=8).step(0, [62]))
    s = Song(length=2).step(0, slot=1, div=4, pause=16, mute=mute).step(1, slot=2, div=4)
    dut.install_song(0, s)
    dut.load_song(0)
    dut.settings(clkin=True)
    dut.use("song", CYCLE=1)
    dut.settle(100, 2)
    dut.play()
    log = dut.stepped(quiet_ms=8).tick(130)
    ev = log.events(lambda m: m.is_note_on(1, 62))
    return ev[0][0] if ev else None


@pytest.mark.tid("D11", "P1")
def test_d11_mute_lasts_as_long_as_the_pattern(dut, rec):
    """Шаг с паттерном длины 8 (DIV 1/16 ⇒ 48 тиков): MUTE не должен менять длительность шага песни."""
    normal = _song_step_duration(dut, mute=0)
    dut.reset()
    muted = _song_step_duration(dut, mute=1)
    rec("тик первой ноты следующего шага (обычный / MUTE)", (normal, muted))
    assert normal is not None and muted is not None
    assert normal == muted, f"следующий шаг начался на тике {normal} после обычного и на {muted} после заглушённого"


def _note_len_ticks(log):
    on = _first(log, lambda m: m.is_note_on(1, 60))
    off = _first(log, lambda m: m.is_note_off(1, 60))
    return None if on is None or off is None else off - on


@pytest.mark.tid("D12", "P1")
@pytest.mark.design
def test_d12_song_ignores_gate(dut, rec):
    """Один и тот же паттерн при GATE=20: в SEQ нота короткая, в SONG тянется до следующего шага."""
    dut.install_pattern(0, straight(16, note=60))
    dut.install_song(0, Song(length=2).step(0, slot=1, div=4).step(1, slot=0, pause=64))
    dut.load_pattern(0)
    dut.settings(clkin=True)
    dut.use("mel", GATE=20)
    dut.settle(100, 2)
    dut.play()
    seq_len = _note_len_ticks(dut.stepped(quiet_ms=12).tick(20))
    dut.reset()
    dut.install_song(0, Song(length=2).step(0, slot=1, div=4).step(1, slot=0, pause=64))
    dut.load_song(0)
    dut.settings(clkin=True)
    dut.use("song", CYCLE=1)
    dut.settle(100, 2)
    dut.play()
    song_len = _note_len_ticks(dut.stepped(quiet_ms=12).tick(20))
    rec("длина ноты, тиков (SEQ при GATE=20 / SONG)", (seq_len, song_len))
    assert seq_len == song_len, f"SEQ {seq_len} тиков, SONG {song_len} тиков"


@pytest.mark.tid("D16", "P1")
def test_d16_song_last_pattern_step_not_cut(dut, rec):
    """В песне нота на последнем шаге паттерна должна звучать, а не выключаться в тот же тик."""
    dut.install_pattern(0, Pattern(length=4).step(3, [64]))
    dut.install_song(0, Song(length=2).step(0, slot=1, div=4).step(1, slot=0, pause=64))
    dut.load_song(0)
    dut.settings(clkin=True)
    dut.use("song", CYCLE=1)
    dut.settle(100, 2)
    dut.play()
    log = dut.stepped(quiet_ms=8).tick(40)
    on = _first(log, lambda m: m.is_note_on(1, 64))
    off = _first(log, lambda m: m.is_note_off(1, 64))
    rec("тик NoteOn / NoteOff ноты последнего шага", (on, off))
    assert on is not None, "нота последнего шага не прозвучала"
    assert off is not None and off > on, f"нота последнего шага выключена в тот же тик ({on} / {off})"


@pytest.mark.tid("D15", "P2")
@pytest.mark.design
def test_d15_song_rev_starts_from_last_step(dut):
    dut.install_song(0, Song(length=4).step(0, slot=1).step(1, slot=1).step(2, slot=1).step(3, slot=1))
    dut.load_song(0)
    dut.use("song", MODE=1)
    dut.play()
    step = dut.state()["song"]["step"]
    assert step == 3, f"REV стартует с шага {step + 1} вместо 4 (SEQ в REV стартует с последнего)"
