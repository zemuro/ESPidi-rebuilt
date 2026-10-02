"""Фикстуры pytest: устройство, MIDI, отчёт.

Тесты формулируют ПРАВИЛЬНОЕ поведение. Провал assert = дефект подтверждён на железе,
успех = гипотеза не воспроизвелась. Итоги пишутся в tools/results/<время>/summary.md.
"""
from __future__ import annotations

import json
import os
import sys
import subprocess
import time
from pathlib import Path

import pytest

from esptest import analysis, thresholds
from esptest.console import Console, DeviceError, find_serial_port
from esptest.dut import Dut
from esptest.midi import Midi, find_ports, probe_ports

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
PIO = Path.home() / ".platformio" / "penv" / "Scripts" / "pio.exe"


def pytest_addoption(parser):
    g = parser.getgroup("espidi")
    g.addoption("--serial", default=os.environ.get("ESPIDI_SERIAL"), help="COM-порт устройства")
    g.addoption("--midi-in", default=os.environ.get("ESPIDI_MIDI_IN"),
                help="подстрока имени MIDI-порта ПК-ВХОДА (куда пишет MIDI OUT ESPidi)")
    g.addoption("--midi-out", default=os.environ.get("ESPIDI_MIDI_OUT"),
                help="подстрока имени MIDI-порта ПК-ВЫХОДА (в MIDI IN ESPidi)")
    g.addoption("--flash", action="store_true", help="собрать и залить окружение `test` перед прогоном")
    g.addoption("--long", action="store_true", help="длинные прогоны (в 6 раз дольше)")
    g.addoption("--allow-wipe", action="store_true",
                help="разрешить тесты, стирающие flash устройства (factory reset: настройки и паттерны)")


def pytest_configure(config):
    config.addinivalue_line("markers", "wipes: тест стирает flash устройства (нужен --allow-wipe)")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "wipes" in item.keywords and not config.getoption("--allow-wipe"):
            item.add_marker(pytest.mark.skip(reason="стирает flash устройства; добавьте --allow-wipe"))
        if "long" in item.keywords and not config.getoption("--long"):
            item.add_marker(pytest.mark.skip(reason="длинный прогон; добавьте --long"))


# ---------------------------------------------------------------- устройство

def _flash(port: str | None):
    cmd = [str(PIO), "run", "-d", str(REPO), "-e", "test", "-t", "upload"]
    if port:
        cmd += ["--upload-port", port]
    print("\n[flash]", " ".join(cmd))
    subprocess.run(cmd, check=True)
    time.sleep(3.0)  # дать USB перечислиться


@pytest.fixture(scope="session")
def console(request):
    port = request.config.getoption("--serial") or find_serial_port()
    if request.config.getoption("--flash"):
        _flash(port)
        port = request.config.getoption("--serial") or find_serial_port()
    if not port:
        pytest.skip("устройство (USB Serial ESP32-C3) не найдено; задайте --serial COMx")
    con = Console(port)
    try:
        con.open()
        info = con.handshake()
    except Exception as e:
        pytest.skip(f"нет ответа консоли на {port}: {e}. Залейте окружение test: pio run -e test -t upload")
    if info.get("fw") != "test":
        pytest.skip("на устройстве не тестовая прошивка (pio run -e test -t upload или --flash)")
    con.virt(True)
    yield con
    try:
        con.virt(False)
    except Exception:
        pass
    con.close()


@pytest.fixture(scope="session")
def midi(request):
    i, o, ins, outs = find_ports(request.config.getoption("--midi-in"), request.config.getoption("--midi-out"))
    if i is None or o is None:
        pytest.skip(f"MIDI-порты не определены. Входы ПК: {ins}; выходы ПК: {outs}. "
                    "Задайте --midi-in / --midi-out (подстрока имени).")
    try:
        probe_ports(i, o)
        m = Midi(i, o)
    except Exception as e:
        pytest.exit(f"MIDI: {e}", returncode=3)
    yield m
    m.close()


@pytest.fixture
def dut(console, midi):
    d = Dut(console, midi)
    d.reset()
    yield d


@pytest.fixture(scope="session")
def run_s(request):
    s = thresholds.SHORT_RUN_S
    return s * (thresholds.LONG_FACTOR if request.config.getoption("--long") else 1)


# ---------------------------------------------------------------- метрики и отчёт

@pytest.fixture
def rec(request):
    """rec("ключ", значение) — сохранить метрику в отчёт."""
    def _rec(key, value):
        request.node.user_properties.append((key, value))
    return _rec


@pytest.fixture(scope="session")
def noise_floor(console, midi):
    """Шумовой пол стенда: джиттер Clock в простое (внутренний темп 120, CLKOUT ON, ничего не играет)."""
    d = Dut(console, midi)
    d.reset()
    d.settings(clkout=True)
    d.settle(200, 3)
    console.stats_reset()
    msgs = midi.capture(6.0)
    times = analysis.clock_times(msgs)
    if len(times) < 50:
        pytest.skip("Clock не принят на ПК: проверьте кабель MIDI OUT → ПК и порт --midi-in")
    j = analysis.jitter(times, 120)
    d.reset()
    return j


RESULTS: list[dict] = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    out = yield
    rep = out.get_result()
    if item.get_closest_marker("unit"):
        return
    if rep.when == "setup" and rep.skipped or rep.when == "call" or (rep.when == "setup" and rep.failed):
        tid = next((m.args for m in item.iter_markers("tid")), ("?", "?"))
        design = any(item.iter_markers("design"))
        if rep.skipped:
            status = "SKIP"
        elif rep.passed:
            status = "OK"
        elif call.excinfo is not None and call.excinfo.errisinstance(AssertionError):
            status = "UNCLEAR" if design else "DEFECT"
        else:
            status = "ERROR"
        msg = ""
        if rep.failed and call.excinfo is not None:
            msg = str(call.excinfo.value).strip().splitlines()[0][:300] if str(call.excinfo.value).strip() else ""
        if rep.skipped and isinstance(rep.longrepr, tuple):
            msg = str(rep.longrepr[2])[:200]
        RESULTS.append(dict(id=tid[0], prio=tid[1] if len(tid) > 1 else "", name=item.name, status=status,
                            msg=msg, props=[(k, v) for k, v in item.user_properties]))


_LABEL = {"DEFECT": "✔ ДЕФЕКТ подтверждён", "OK": "✘ не воспроизведено", "UNCLEAR": "≈ поведение — уточнить у Евгения",
          "SKIP": "— пропущен", "ERROR": "! ошибка стенда"}


def pytest_sessionfinish(session, exitstatus):
    if not RESULTS:
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = ROOT / "results" / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1, default=str), "utf-8")
    lines = [f"# Результаты прогона {stamp}", "",
             "| ID | Пр. | Тест | Результат | Замечание |", "|---|---|---|---|---|"]
    for r in sorted(RESULTS, key=lambda x: (x["id"][:1], int("".join(c for c in x["id"][1:] if c.isdigit()) or 0), x["id"])):
        lines.append(f"| {r['id']} | {r['prio']} | `{r['name']}` | {_LABEL[r['status']]} | {r['msg'].replace('|', '/')} |")
    lines += ["", "## Метрики", ""]
    for r in RESULTS:
        if r["props"]:
            lines.append(f"**{r['id']}** `{r['name']}`")
            for k, v in r["props"]:
                lines.append(f"- {k}: {v}")
            lines.append("")
    (outdir / "summary.md").write_text("\n".join(lines), "utf-8")
    latest = ROOT / "results" / "latest"
    latest.mkdir(exist_ok=True)
    (latest / "summary.md").write_text("\n".join(lines), "utf-8")
    (latest / "results.json").write_text((outdir / "results.json").read_text("utf-8"), "utf-8")
    print(f"\n[esptest] отчёт: {outdir / 'summary.md'}")
