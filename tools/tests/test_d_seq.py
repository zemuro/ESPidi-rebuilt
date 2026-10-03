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
def test_d7_unsaved_edits_guard_pattern_switch(dut, rec):
    """Решение автора: с несохранёнными правками первый поворот PTRN не переключает, а мигает SAVE;
    щелчки того же поворота (быстро подряд) — тоже нет; поворот после паузы, пока мигает, — переключает."""
    dut.install_pattern(1, straight(8, note=70))
    dut.use("mel")
    dut.step_edit()
    dut.rec()
    dut.midi.note_on(1, 61, 100)
    time.sleep(0.2)
    dut.midi.note_off(1, 61)
    assert dut.c.seqdump(0, 1)[0]["n"] == [61]
    assert dut.state()["mel"]["dirty"]
    slot0 = dut.state()["mel"]["ptrn"]           # слот, оставшийся от предыдущих тестов
    assert slot0 != 1

    dut.c.param("PTRN", 2)                       # первый поворот: только предупреждение
    dut.c.param("PTRN", 2)                       # тот же поворот, следующий щелчок
    s = dut.state()
    first = (s["mel"]["ptrn"], s["warn"], dut.c.seqdump(0, 1)[0]["n"])
    time.sleep(0.5)
    dut.c.param("PTRN", 2)                       # новый поворот после паузы — переключаем
    time.sleep(0.2)
    s = dut.state()
    second = (s["mel"]["ptrn"], s["warn"], dut.c.seqdump(0, 1)[0]["n"])
    rec("(слот, мигает SAVE, шаг 0): после первого поворота / после повторного", (first, second))
    assert first == (slot0, 1, [61]), "первый поворот должен только предупредить, правки на месте"
    assert second == (1, 0, [70]), "повторный поворот должен переключить паттерн"


@pytest.mark.tid("D20", "P1")
def test_d20_unsaved_warning_expires(dut, rec):
    """Предупреждение живёт ~2 с: поворот после него снова только предупреждает (SONG — то же правило)."""
    dut.use("song", LENGTH=5)                    # LENGTH песни — несохранённая правка
    assert dut.state()["song"]["dirty"]
    song0 = dut.state()["song"]["song"]
    dut.c.param("SONG", song0 + 2)
    warned = dut.state()["warn"]
    time.sleep(2.5)
    expired = dut.state()["warn"]
    dut.c.param("SONG", song0 + 2)               # окно истекло — опять только предупреждение
    s = dut.state()
    rec("мигает / погасло / снова мигает, слот", (warned, expired, s["warn"], s["song"]["song"]))
    assert (warned, expired, s["warn"]) == (1, 0, 1)
    assert s["song"]["song"] == song0, "песня переключилась, хотя правки не сохранены"
    time.sleep(0.5)
    dut.c.param("SONG", song0 + 2)               # повторный поворот — переключаем
    assert dut.state()["song"]["song"] == song0 + 1


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
def test_d12_song_uses_pattern_gate(dut, rec):
    """Решение автора: GATE — свойство паттерна. Паттерн с GATE 64 (1/16 = 6 тиков ⇒ 3 тика)
    звучит одинаково и в секвенсоре, и в песне (раньше в песне нота тянулась до следующего шага)."""
    dut.install_pattern(0, straight(16, note=60, gate=64))
    dut.install_song(0, Song(length=2).step(0, slot=1, div=4).step(1, slot=0, pause=64))
    dut.load_pattern(0)
    dut.settings(clkin=True)
    dut.use("mel")
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
    rec("длина ноты, тиков (SEQ / SONG), GATE паттерна 64", (seq_len, song_len))
    assert (seq_len, song_len) == (3, 3)


@pytest.mark.tid("D21", "P1")
def test_d21_gate_belongs_to_pattern(dut, rec):
    """GATE загружается с паттерном; правка GATE — несохранённая правка паттерна."""
    dut.install_pattern(0, straight(8, gate=30))
    dut.install_pattern(1, straight(8, gate=100))
    dut.load_pattern(0)
    dut.use("mel")
    g0 = dut.state()["mel"]["gate"]
    dut.c.param("PTRN", 2)
    time.sleep(0.2)
    g1 = dut.state()["mel"]["gate"]
    dut.c.param("GATE", 50)
    dirty = dut.state()["mel"]["dirty"]
    rec("GATE паттерна 1 / 2, правка помечает паттерн", (g0, g1, dirty))
    assert (g0, g1) == (30, 100)
    assert dirty, "правка GATE не помечает паттерн как несохранённый"


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
def test_d15_song_rev_plays_backwards(dut, rec):
    """Решение автора: песня в REV стартует с последнего шага, как секвенсор. Шаги A B C D (ноты
    60–63), CYCLE=OFF: звучит D C B A, и песня останавливается (раньше — A D C B)."""
    for i in range(4):
        dut.install_pattern(i, Pattern(length=1).step(0, [60 + i]))
    s = Song(length=4)
    for i in range(4):
        s.step(i, slot=i + 1, div=5)          # 1/32 = 3 тика на шаг
    dut.install_song(0, s)
    dut.load_song(0)
    dut.settings(clkin=True)
    dut.use("song", MODE=1, CYCLE=0)
    dut.settle(100, 2)
    dut.play()
    start = dut.state()["song"]["step"]
    log = dut.stepped(quiet_ms=12).tick(20)
    order = [m.d1 - 60 for _, m in log.events(lambda m: m.is_note_on(1))]
    names = "".join("ABCD"[i] for i in order if 0 <= i < 4)
    rec("стартовый шаг / порядок", (start + 1, names))
    assert start == 3, f"REV стартует с шага {start + 1} вместо 4"
    assert names == "DCBA", f"порядок {names}, ожидалось DCBA"
    assert not dut.state()["song"]["en"], "с CYCLE=OFF песня должна остановиться после A"


# ---------------------------------------------------------------- смена PTRN во время игры (SWAP)
# Паттерн A — 16 шагов, ноты 40+шаг; B — 6 шагов, ноты 80+шаг. Смена на втором проходе A, пока звучит
# 28-й шаг от PLAY (глобальный номер 27). Правило A: позиция нового паттерна = (шагов с PLAY) mod длина,
# т. е. следующий шаг B — 28 mod 6 = 4. Иные правила дали бы 0 («с начала») или 12 mod 6 = 0
# («позиция mod длина»). Проверка — по состоянию прошивки (паттерн, сыгранный и следующий шаг).

SWAP = {"NOW": 0, "NEXT": 1, "END": 2}


def _swap_setup(dut, mode, seq_mode=0):
    a = Pattern(length=16)
    for i in range(16):
        a.step(i, [40 + i])
    b = Pattern(length=6)
    for i in range(6):
        b.step(i, [80 + i])
    dut.install_pattern(0, a)
    dut.install_pattern(1, b)
    dut.load_pattern(0)
    dut.settings(clkin=True)
    dut.use("mel", MODE=seq_mode, SWAP=SWAP[mode])
    dut.settle(100, 2)
    dut.play()
    return dut.stepped(quiet_ms=12)


def _mel(dut):
    m = dut.state()["mel"]
    return (m["ptrn"], m["swpend"], m["last"], m["step"])


@pytest.mark.tid("D17", "P1")
@pytest.mark.parametrize("mode,expect", [
    #        сразу после смены   после шага 28        после шага 32
    ("NOW", [(1, 0, 3, 4), (1, 0, 4, 5), (1, 0, 2, 3)]),
    ("NEXT", [(0, 1, 11, 12), (1, 0, 4, 5), (1, 0, 2, 3)]),
    ("END", [(0, 1, 11, 12), (0, 1, 12, 13), (1, 0, 0, 1)]),
])
def test_d17_pattern_swap_modes(dut, rec, mode, expect):
    """SWAP: NOW — шаг B звучит сразу; NEXT — со следующего шага; END — после конца A, с начала B.
    Кортеж: (слот, смена ждёт, сыгранный шаг, следующий шаг)."""
    clk = _swap_setup(dut, mode)
    clk.tick(27 * 6 + 2)                 # звучит шаг 27 от PLAY (позиция 11 в A), 2 такта внутри шага
    dut.c.param("PTRN", 2)
    time.sleep(0.15)                     # файл читается в основном цикле
    got = [_mel(dut)]
    clk.tick(5)                          # такты 164–168: на 168-м — граница, сыгран шаг 28
    got.append(_mel(dut))
    clk.tick(24)                         # сыграны шаги 29–32
    got.append(_mel(dut))
    rec("(слот, ждёт, сыгран, следующий)", got)
    assert got == expect


@pytest.mark.tid("D18", "P1")
def test_d18_pattern_swap_pend_rule_a(dut, rec):
    """PEND, NEXT: B встаёт туда, где был бы, играя с PLAY: шаг 28 при периоде 10 → позиция 2, ход назад."""
    clk = _swap_setup(dut, "NEXT", seq_mode=2)
    clk.tick(27 * 6 + 2)
    dut.c.param("PTRN", 2)
    time.sleep(0.15)
    clk.tick(5)
    got = _mel(dut)
    rec("(слот, ждёт, сыгран, следующий)", got)
    assert got == (1, 0, 2, 1)


@pytest.mark.tid("D19", "P1")
def test_d19_stop_while_swap_pending(dut, rec):
    """END: остановили, пока смена ждёт конца паттерна, — выбранный паттерн становится текущим."""
    clk = _swap_setup(dut, "END")
    clk.tick(3 * 6 + 2)
    dut.c.param("PTRN", 2)
    time.sleep(0.15)
    assert dut.state()["mel"]["swpend"] == 1
    dut.play()                           # STOP
    m = dut.state()["mel"]
    rec("слот / ждёт / шаг 0", (m["ptrn"], m["swpend"], dut.c.seqdump(0, 1)[0]["n"]))
    assert (m["ptrn"], m["swpend"]) == (1, 0)
    assert dut.c.seqdump(0, 1)[0]["n"] == [80]
