"""python -m esptest ports | probe | flood <порт> [сек] — быстрая диагностика стенда."""
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
    elif cmd == "flood":
        # python -m esptest flood "<подстрока имени MIDI-выхода ПК>" [секунды]
        # Непрерывный поток 0xF8 на выход ПК: на пине 5 DIN среднее напряжение падает,
        # на пине 4 — нет. Так мультиметром определяется тип TRS-переходника (A/B).
        import time
        import rtmidi
        hint = sys.argv[2] if len(sys.argv) > 2 else None
        secs = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0
        _, o, _, outs = find_ports(None, hint)
        if o is None:
            print("Укажите порт подстрокой имени. Выходы ПК:", outs)
            return
        out = rtmidi.MidiOut()
        out.open_port(o)
        print(f"Поток Clock на «{outs[o]}» {secs:.0f} с. Красный щуп — кончик, чёрный — кольцо: "
              "плюс → Type B, минус → Type A. Ctrl+C — стоп.")
        end = time.time() + secs
        try:
            while time.time() < end:
                for _ in range(64):
                    out.send_message([0xF8])
                time.sleep(0.005)  # ≈ 12 800 байт/с запрошено — линия 31250 бод занята полностью
        except KeyboardInterrupt:
            pass
        out.close_port()
    elif cmd == "dutflood":
        # python -m esptest dutflood [сек] — ESPidi сама забивает свой MIDI OUT байтами Clock
        secs = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        c = Console(find_serial_port()).open()
        c.handshake()
        c.cmd("txflood", secs)
        print(f"ESPidi шлёт поток на MIDI OUT {secs} с. На гнезде DIN переходника: красный — пин 4, чёрный — пин 5.")
        c.close()
    elif cmd == "probe":
        port = find_serial_port()
        print("порт:", port)
        c = Console(port).open()
        print(c.handshake())
        print(c.state())
        c.close()
    else:
        print(__doc__)


main()
