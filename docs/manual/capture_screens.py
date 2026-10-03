"""Снимает настоящие кадры экрана ESPidi для мануала (нужна тестовая прошивка env:test).

    cd tools && python ../docs/manual/capture_screens.py [COM-порт]

Сохраняет screens/raw.json: имя кадра → буфер SSD1306 (512 байт, hex). Картинки делает
render_screens.py. Скрипт перезаписывает паттерны 1–3 и песню 1 на устройстве.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())
from esptest.console import Console          # noqa: E402
from esptest.dut import Dut                  # noqa: E402
from esptest.midi import Midi, find_ports    # noqa: E402
from esptest.patterns import Pattern, Song   # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screens", "raw.json")
port = sys.argv[1] if len(sys.argv) > 1 else "COM5"

c = Console(port); c.open(); c.handshake()
i, o, *_ = find_ports("USB2.0-MIDI", "USB2.0-MIDI")
m = Midi(i, o)
d = Dut(c, m)
d.reset()
c.virt(True)
shots = {}


def shot(name, wait=0.25):
    time.sleep(wait)
    shots[name] = c.screen().raw.hex()
    print("снят кадр:", name, flush=True)


# --- материал: бас-линия, аккордовый паттерн, песня
bass = Pattern(length=16)
for k, n in zip(range(0, 16, 2), (36, 36, 43, 36, 39, 36, 43, 46)):
    bass.step(k, [n])
bass.step(4, [43], cc=[(74, 90)])
bass.step(10, [43], tie=True)
chords = Pattern(length=16)
chords.step(0, [60, 64, 67], cc=[(74, 100), (1, 20)], tie=True, transpose=3)
chords.step(4, [62, 65, 69])
chords.step(8, [60, 64, 67])
chords.step(12, [59, 62, 67])
d.install_pattern(0, bass)
d.install_pattern(1, chords)
d.install_pattern(2, Pattern(length=8).step(0, [48]))
song = Song(length=8)
for k, s in enumerate((1, 1, 2, 2, 0, 1, 3, 2)):
    song.step(k, slot=s, tr=(5 if k == 5 else 0), div=4, pause=8)
d.install_song(0, song)
d.load_pattern(0)
d.load_song(0)
d.settings(clkin=False, clkout=False)

# --- ARP
c.app(0)
c.param("MODE", 2)
c.param("OCT", 2)
d.play()
for n in (60, 64, 67):
    m.note_on(1, n, 100)
shot("arp_play", 0.6)
c.param("DIV", 4)
c.click("enc")                      # курсор стоит на DIV — режим правки
shot("arp_edit")
c.click("enc")
for n in (60, 64, 67):
    m.note_off(1, n)
d.play()

# --- меню приложений
c.click("enc", long=True)
time.sleep(0.4)
c.enc(1)
shot("menu")
c.click("enc")

# --- SEQUENCER
c.app(1)
d.load_pattern(0)
c.app(1)
c.param("BPM", 120)
d.play()
shot("seq_play", 1.0)
c.param("SWAP", 1)
c.click("enc")
shot("seq_swap_edit")
c.click("enc")
d.play()
d.rec()
shot("seq_rec")
d.rec()

# мигающий SAVE: правка без сохранения, затем попытка сменить паттерн
c.param("LENGTH", 15)
c.param("LENGTH", 16)
c.param("PTRN", 2)
frames = []
for _ in range(6):
    frames.append(c.screen().raw.hex())
    time.sleep(0.11)
shots["seq_save_warn_a"], shots["seq_save_warn_b"] = frames[0], next(f for f in frames if f != frames[0])
print("снят кадр: seq_save_warn", flush=True)
time.sleep(2.3)

# STEP EDIT на аккордовом паттерне
d.load_pattern(1)
c.app(1)
d.step_edit()
time.sleep(0.2)
for _ in range(8):                  # к шагу 1 — там аккорд с Tie, транспонированием и CC
    e = c.state()["mel"]["edit"]
    if e == 0:
        break
    c.enc(-1 if e < 32 else 1)
    time.sleep(0.15)
print("шаг редактора:", c.state()["mel"]["edit"], c.seqdump(0, 1))
shot("seq_stepedit")
d.step_edit()

# --- SONG
c.app(2)
d.load_song(0)
c.app(2)
shot("song_main")
d.play()
shot("song_play", 1.2)
d.play()
d.step_edit()
c.enc(5)
shot("song_stepedit")
d.step_edit()

# --- MONITOR
c.app(3)
for n, v in ((48, 90), (55, 70), (60, 110), (64, 64)):
    m.note_on(1, n, v)
m.cc(1, 74, 87)
m.cc(1, 1, 40)
m.cc(2, 7, 100)
shot("monitor", 0.4)
for n in (48, 55, 60, 64):
    m.note_off(1, n)

# --- SETTINGS, HELP, внешний Clock
c.app(4)
shot("settings")
c.param("CLKIN", 1)
c.app(1)
shot("seq_ext")
c.app(4)
c.param("CLKIN", 0)
c.click("lr")
c.enc(1)
c.click("enc")
shot("help")
c.click("enc")

c.app(1)
c.virt(False)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(shots, f, indent=1)
print("сохранено:", OUT, len(shots), "кадров")
c.close()
