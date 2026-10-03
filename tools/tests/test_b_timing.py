"""B. Тайминг (главный вывод ревью: вынос CLOCK в высокоприоритетную задачу).

Метод: внутренний темп 120 BPM, CLKOUT ON. Два независимых измерителя:
  * ПК — интервалы байтов 0xF8 на MIDI OUT (шумовой пол стенда вычитается через noise_floor);
  * ESP32 — гистограммы внутри прошивки (опоздание тика, зазоры обслуживания, длительность
    блокирующих участков), не зависят от кабеля и Windows.
"""
import time

import pytest

from esptest import analysis, thresholds
from esptest.analysis import clock_times, jitter, pc_limit_ms
from esptest.patterns import Pattern, Song, straight


def _verdict(dut, msgs, noise, rec, label):
    """Общая проверка: интервалы Clock на ПК + метрики прошивки. Возвращает список нарушений."""
    st = dut.c.stats()
    rec("scopes (макс, мкс)", {k: v["max"] for k, v in st["scopes"].items()})
    rec("опоздание тика, гист.", analysis.fmt_hist(st["late"], analysis.EDGES_LATE) or "—")
    rec("зазор обслуживания, гист.", analysis.fmt_hist(st["gap"], analysis.EDGES_MS) or "—")
    rec("итерация loop, гист.", analysis.fmt_hist(st["loop"], analysis.EDGES_MS) or "—")
    rec("loop max, мкс", st["loop"]["max"])
    rec("EEPROM commit", st["commits"])
    times = clock_times(msgs)
    fails = []
    if len(times) < 20:
        return st, fails + [f"{label}: Clock почти не принят ({len(times)})"]
    j = jitter(times, 120)
    limit = pc_limit_ms(noise)
    rec("ПК: Clock", str(j))
    rec("ПК: предел интервала, мс", f"{limit:.1f}")
    if j.max > limit:
        fails.append(f"ПК: интервал Clock {j.max:.1f} мс > {limit:.1f} мс (выбросов {j.over})")
    if st["late"]["max"] > thresholds.MAX_TICK_LATE_US:
        fails.append(f"ESP: опоздание тика {st['late']['max']} мкс > {thresholds.MAX_TICK_LATE_US}")
    return st, fails


def _begin(dut):
    dut.settings(clkout=True)
    dut.c.flushsave()          # сохранение от смены настроек — до замера, а не внутри него
    time.sleep(0.6)
    dut.settle(100, 2)
    dut.c.stats_reset()
    return dut.midi.mark()


@pytest.mark.tid("B1", "P0")
def test_b1_idle_baseline(dut, noise_floor, rec, run_s):
    """Контроль: ничего не играет. Если не проходит — стенд шумит, остальным B-тестам верить нельзя."""
    dut.settings(clkout=True)
    dut.c.flushsave()          # иначе отложенное сохранение (B8) попадёт в окно контрольного замера
    time.sleep(0.6)
    dut.settle(100, 2)
    dut.c.stats_reset()
    msgs = dut.midi.capture(run_s)
    st, fails = _verdict(dut, msgs, noise_floor, rec, "idle")
    assert not fails, "; ".join(fails)


@pytest.mark.tid("B2", "P0")
def test_b2_arp_running_with_redraw(dut, noise_floor, rec, run_s):
    """ARP играет: каждый шаг помечает экран «грязным» → кадр OLED (≈12 мс по I²C) в основном цикле."""
    dut.use("arp", DIV=4)
    m0 = _begin(dut)
    dut.play()
    for n in (60, 64, 67):
        dut.midi.note_on(1, n, 100)
    time.sleep(run_s)
    msgs = dut.midi.since(m0)
    st, fails = _verdict(dut, msgs, noise_floor, rec, "arp")
    rec("ui.draw (макс/кол-во)", st["scopes"].get("ui.draw"))
    assert not fails, "; ".join(fails)


@pytest.mark.tid("B3", "P0")
def test_b3_encoder_spin_redraw(dut, noise_floor, rec, run_s):
    """Полная перерисовка на каждый щелчок энкодера (до 25 раз/с)."""
    m0 = _begin(dut)
    end = time.time() + min(run_s, 8)
    while time.time() < end:
        dut.c.enc(1)
        dut.c.enc(-1)
        time.sleep(0.02)
    msgs = dut.midi.since(m0)
    st, fails = _verdict(dut, msgs, noise_floor, rec, "enc")
    assert not fails, "; ".join(fails)


@pytest.mark.tid("B4", "P0")
def test_b4_sequencer_step_stop_all(dut, noise_floor, rec, run_s):
    """SEQ: на каждом шаге без Tie шлётся CC123 на 16 каналов (≈48 байт ≈ 15 мс) — смотрим влияние на сетку."""
    dut.install_pattern(0, straight(16, note=60, gate=80))
    dut.load_pattern(0)
    dut.use("mel")
    dut.settle(100, 2)
    m0 = _begin(dut)
    dut.play()
    time.sleep(run_s)
    msgs = dut.midi.since(m0)
    st, fails = _verdict(dut, msgs, noise_floor, rec, "seq")
    cc123 = analysis.count(msgs, lambda m: m.is_cc(123))
    ons = [m.t for m in msgs if m.is_note_on(1, 60)]
    rec("CC123 всего / NoteOn(60)", f"{cc123} / {len(ons)}")
    if len(ons) > 4:
        iv = analysis.intervals_ms(ons)
        dev = max(abs(x - 125.0) for x in iv)
        rec("NoteOn: макс. отклонение от 125 мс", f"{dev:.1f} мс")
        if dev > thresholds.MAX_TICK_LATE_US / 1000 + thresholds.PC_JITTER_MARGIN_MS:
            fails.append(f"NoteOn уходит от сетки на {dev:.1f} мс")
    assert not fails, "; ".join(fails)


@pytest.mark.tid("B5", "P0")
def test_b5_song_pattern_boundary(dut, noise_floor, rec, run_s):
    """SONG: на стыке паттернов 128×NoteOff + CC123 (≈123 мс) и перечитывание файла из LittleFS в тике."""
    dut.install_pattern(0, straight(16, note=60))
    dut.install_song(0, Song(length=2).step(0, slot=1, div=4).step(1, slot=1, div=4))
    dut.load_song(0)
    dut.use("song", CYCLE=1)
    dut.settle(100, 2)
    m0 = _begin(dut)
    dut.play()
    time.sleep(max(run_s, 8))
    msgs = dut.midi.since(m0)
    st, fails = _verdict(dut, msgs, noise_floor, rec, "song")
    rec("song.stopAll / song.loadBuf (макс, мкс)",
        (st["scopes"].get("song.stopAll", {}).get("max"), st["scopes"].get("song.loadBuf", {}).get("max")))
    assert not fails, "; ".join(fails)


@pytest.mark.tid("B6", "P0")
def test_b6_arp_release_last_key(dut, noise_floor, rec, run_s):
    """ARP: отпускание последней клавиши → stopSounding = 128×NoteOff + CC123 (≈123 мс)."""
    dut.use("arp", DIV=4)
    m0 = _begin(dut)
    dut.play()
    for _ in range(max(3, int(run_s // 3))):
        dut.midi.note_on(1, 60, 100)
        time.sleep(1.2)
        dut.midi.note_off(1, 60)
        time.sleep(1.2)
    msgs = dut.midi.since(m0)
    st, fails = _verdict(dut, msgs, noise_floor, rec, "arp-release")
    rec("arp.stopSounding (макс, мкс)", st["scopes"].get("arp.stopSounding", {}).get("max"))
    assert not fails, "; ".join(fails)


@pytest.mark.tid("B7", "P0")
def test_b7_pattern_switch_while_playing(dut, noise_floor, rec, run_s):
    """Смена PTRN во время игры: чтение файла LittleFS + CC123×16 из UI-пути."""
    for s in range(4):
        dut.install_pattern(s, straight(16, note=60 + s))
    dut.load_pattern(0)
    dut.use("mel")
    dut.settle(100, 2)
    m0 = _begin(dut)
    dut.play()
    end = time.time() + min(run_s, 10)
    v = 1
    while time.time() < end:
        v = v % 4 + 1
        dut.c.param("PTRN", v)
        time.sleep(0.45)
    msgs = dut.midi.since(m0)
    st, fails = _verdict(dut, msgs, noise_floor, rec, "ptrn")
    rec("mel.load (макс, мкс)", st["scopes"].get("mel.load", {}).get("max"))
    assert not fails, "; ".join(fails)


@pytest.mark.tid("B8", "P0")
def test_b8_no_autosave_while_playing(dut, noise_floor, rec):
    """Решение: пока что-то играет, настройки во флеш не пишутся (запись останавливает цикл
    на 3–17 мс); сохранение — после STOP."""
    dut.use("arp", DIV=4)
    m0 = _begin(dut)
    dut.play()
    for n in (60, 64, 67):
        dut.midi.note_on(1, n, 100)
    dut.c.param("GATE", 90)                 # → scheduleGlobalSave()
    time.sleep(7.0)                         # > SAVE_DELAY_MS (5 с)
    msgs = dut.midi.since(m0)
    st, fails = _verdict(dut, msgs, noise_floor, rec, "autosave")
    commits_playing = st["commits"]
    dut.play()                              # STOP
    for n in (60, 64, 67):
        dut.midi.note_off(1, n)
    time.sleep(1.5)
    commits_after = dut.c.stats()["commits"]
    rec("EEPROM commit во время игры / после STOP", f"{commits_playing} / {commits_after}")
    assert commits_playing == 0, f"во время игры было {commits_playing} записей во флеш"
    assert commits_after >= 1, "после STOP настройки не сохранились"
    assert not fails, "; ".join(fails)


@pytest.mark.tid("B11", "P1")
def test_b11_tempo_accuracy(dut, rec, run_s):
    dut.settings(clkout=True)
    dut.settle(100, 2)
    msgs = dut.midi.capture(run_s)
    t = clock_times(msgs)
    assert len(t) > 100
    mean_iv = (t[-1] - t[0]) / (len(t) - 1) * 1000
    nominal = 60000 / (120 * 24)
    rel = abs(mean_iv - nominal) / nominal
    tol = max(thresholds.TEMPO_REL_TOLERANCE, 0.005 / (t[-1] - t[0]))
    rec("средний интервал / номинал", f"{mean_iv:.4f} / {nominal:.4f} мс; отклонение {rel*100:.4f} %")
    assert rel <= tol, f"темп уходит на {rel*100:.3f} % (допуск {tol*100:.3f} %)"


@pytest.mark.tid("B12", "P1")
@pytest.mark.design
def test_b12_tap_tempo_averages_last_three(dut, rec):
    """Решение автора: TAP — среднее последних трёх интервалов. 600/600/600/800 мс → (600+600+800)/3 ≈ 667 мс
    = 90 BPM (по последнему интервалу было бы 75)."""
    c = dut.c
    gaps = [0.6, 0.6, 0.6, 0.8]
    # TAP срабатывает на отпускании. Отпускания — по расписанию от общего старта (задержки консоли
    # не накапливаются); ожидаемый темп считаем по фактическим моментам отпусканий.
    start = time.perf_counter() + 0.1
    targets = [start + sum(gaps[:k]) for k in range(len(gaps) + 1)]
    released = []
    for tk in targets:
        time.sleep(max(0, tk - 0.06 - time.perf_counter()))
        c.press("tap")
        time.sleep(max(0, tk - time.perf_counter()))
        t1 = time.perf_counter()
        c.release("tap")
        released.append((t1 + time.perf_counter()) / 2)
    time.sleep(0.15)
    bpm = dut.state()["bpm"]
    real = [b - a for a, b in zip(released, released[1:])]
    mean_bpm = 60.0 / (sum(real[-3:]) / 3)
    rec("интервалы, мс", [round(x * 1000) for x in real])
    rec("BPM после TAP / среднее трёх последних интервалов", f"{bpm} / {mean_bpm:.1f}")
    assert abs(bpm - mean_bpm) <= 4, f"BPM={bpm}, по среднему трёх последних интервалов {mean_bpm:.0f}"


@pytest.mark.tid("B13", "P1")
def test_b13_external_clock_lost_hangs_notes(dut):
    """Внешний Clock шёл и пропал, пока нота арпеджио звучит: gate считается в тиках ⇒ без
    реакции на потерю Clock NoteOff не придёт никогда."""
    dut.settings(clkin=True)
    dut.use("arp", DIV=2, GATE=80, THRU=0)
    dut.play()
    clk = dut.stepped(quiet_ms=10)
    clk.tick(12)                          # Clock идёт
    dut.midi.clear()
    dut.midi.note_on(1, 60, 100)          # арп отвечает NoteOn сразу
    clk.tick(3)                           # gate 24·80/127 ≈ 15 тиков — нота ещё звучит
    time.sleep(1.5)                       # Clock пропал > CLOCK_TIMEOUT (0,5 с); клавиша по-прежнему зажата
    hung = analysis.hanging_notes(dut.midi.since(0))
    playing = dut.state()["arp"]["en"]
    dut.midi.note_off(1, 60)
    assert not hung, f"зависшие ноты после потери Clock: {sorted(hung)}"
    assert not playing, "решение автора: при потере Clock приложение останавливается, как по STOP"


@pytest.mark.tid("B14", "P1")
@pytest.mark.design
def test_b14_first_step_plays_on_start(dut, rec):
    """После Start первая нота секвенсора должна звучать на первом тике (доля «раз»), а не на 6-м."""
    dut.install_pattern(0, straight(16, note=60))
    dut.load_pattern(0)
    dut.settings(clkin=True, start=True)
    dut.use("mel")
    dut.settle(100, 2)
    clk = dut.stepped()
    clk.start()
    log = clk.tick(14)
    first = log.events(lambda m: m.is_note_on(1, 60))
    assert first, "NoteOn не пришёл за 14 тиков"
    idx = first[0][0]
    rec("тик первой ноты после Start (0 = сразу)", idx)
    assert idx == 0, f"первая нота на тике {idx} — со сдвигом на {idx} тик(ов) от Start"
