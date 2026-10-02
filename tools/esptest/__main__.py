"""python -m esptest ports | probe — быстрая диагностика стенда."""
import sys

from .console import Console, find_serial_port
from .midi import find_ports


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ports"
    if cmd == "ports":
        from serial.tools import list_ports
        print("COM-порты:")
        for p in list_ports.comports():
            print(f"  {p.device:8} vid={p.vid and hex(p.vid)} {p.description}")
        i, o, ins, outs = find_ports()
        print("\nMIDI-входы ПК (сюда пишет MIDI OUT ESPidi):")
        for n, name in enumerate(ins):
            print(f"  [{n}] {name}" + ("   <- авто" if n == i else ""))
        print("MIDI-выходы ПК (в MIDI IN ESPidi):")
        for n, name in enumerate(outs):
            print(f"  [{n}] {name}" + ("   <- авто" if n == o else ""))
        print("\nАвтоопределение MIDI не сработало? Задайте --midi-in/--midi-out или ESPIDI_MIDI_IN/OUT.")
    elif cmd == "probe":
        port = find_serial_port()
        print("порт:", port)
        c = Console(port).open()
        print(c.ping())
        print(c.state())
        c.close()
    else:
        print(__doc__)


main()
