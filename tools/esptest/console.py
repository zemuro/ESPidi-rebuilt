"""Клиент USB-консоли тестовой прошивки (окружение PlatformIO `test`).

Протокол: строка `<id> <команда> [аргументы]\\n` → ответ `@<id> <json>\\n`.
Остальные строки (`Serial.print` из прошивки) складываются в `console.log`.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field

import serial
from serial.tools import list_ports

ESPRESSIF_VID = 0x303A


class DeviceError(RuntimeError):
    pass


def find_serial_port() -> str | None:
    """Порт ESP32-C3 (USB Serial/JTAG, VID 303A). Bluetooth-порты игнорируются."""
    cands = []
    for p in list_ports.comports():
        desc = (p.description or "") + " " + (p.manufacturer or "")
        if "bluetooth" in desc.lower():
            continue
        if p.vid == ESPRESSIF_VID:
            cands.append(p.device)
        elif re.search(r"USB Serial Device|USB JTAG|ESP32", desc, re.I):
            cands.append(p.device)
    return cands[0] if cands else None


@dataclass
class Screen:
    """Кадр SSD1306 128x32 (буфер Adafruit: страницы по 8 строк, бит 0 — верхний)."""
    raw: bytes
    W: int = 128
    H: int = 32

    def px(self, x: int, y: int) -> bool:
        return bool(self.raw[x + (y // 8) * self.W] >> (y % 8) & 1)

    def lit(self) -> int:
        return sum(bin(b).count("1") for b in self.raw)

    def ascii(self, x0=0, x1=None, y0=0, y1=None) -> str:
        x1 = self.W if x1 is None else x1
        y1 = self.H if y1 is None else y1
        rows = []
        for y in range(y0, y1):
            rows.append("".join("#" if self.px(x, y) else "." for x in range(x0, x1)))
        return "\n".join(rows)

    def region_lit(self, x0, y0, x1, y1) -> int:
        return sum(self.px(x, y) for x in range(x0, x1) for y in range(y0, y1))


@dataclass
class Console:
    port: str
    baud: int = 115200
    timeout: float = 4.0
    log: list = field(default_factory=list)
    _ser: serial.Serial | None = None
    _pending: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _next_id: int = 1
    _stop: bool = False

    # ------------------------------------------------------------ соединение
    def open(self):
        ser = serial.Serial()
        ser.port, ser.baudrate, ser.timeout = self.port, self.baud, 0.05
        ser.dtr = False  # не дёргать сброс USB-Serial/JTAG при открытии
        ser.rts = False
        ser.open()
        self._ser = ser
        self._stop = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        return self

    def close(self):
        self._stop = True
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None

    def _read_loop(self):
        buf = b""
        while not self._stop:
            try:
                chunk = self._ser.read(512)
            except Exception:
                return
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._on_line(line.decode("utf-8", "replace").strip())

    def _on_line(self, line: str):
        m = re.match(r"@(\d+) (.*)$", line)
        if not m:
            if line:
                self.log.append(line)
            return
        with self._lock:
            slot = self._pending.get(int(m.group(1)))
        if slot is not None:
            try:
                slot["resp"] = json.loads(m.group(2))
            except json.JSONDecodeError:
                slot["resp"] = {"err": "bad json: " + m.group(2)[:80]}
            slot["ev"].set()

    # ------------------------------------------------------------ команды
    _IDEMPOTENT = {"ping", "state", "stats", "screen", "eeprom", "seqdump", "songdump", "load", "rxlog", "fs"}

    def cmd(self, name: str, *args, timeout: float | None = None) -> dict:
        try:
            return self._cmd(name, *args, timeout=timeout)
        except DeviceError as e:
            if name in self._IDEMPOTENT and "нет ответа" in str(e):
                return self._cmd(name, *args, timeout=timeout)   # один повтор при потерянном ответе
            raise

    def _cmd(self, name: str, *args, timeout: float | None = None) -> dict:
        with self._lock:
            cid = self._next_id
            self._next_id += 1
            slot = {"ev": threading.Event(), "resp": None}
            self._pending[cid] = slot
        line = f"{cid} {name} " + " ".join(str(a) for a in args)
        self._ser.write((line.strip() + "\n").encode())
        ok = slot["ev"].wait(timeout or self.timeout)
        with self._lock:
            self._pending.pop(cid, None)
        if not ok:
            raise DeviceError(f"нет ответа на '{line.strip()[:60]}'")
        if "err" in slot["resp"]:
            raise DeviceError(f"{name}: {slot['resp']['err']}")
        return slot["resp"]

    # ------------------------------------------------------------ удобные обёртки
    def ping(self) -> dict:
        return self.cmd("ping")

    def handshake(self, tries=6) -> dict:
        """После сброса первый обмен с USB-CDC теряется — повторяем ping."""
        last = None
        for _ in range(tries):
            try:
                return self.cmd("ping", timeout=1.0)
            except DeviceError as e:
                last = e
        raise last

    def state(self) -> dict:
        return self.cmd("state")

    def stats(self) -> dict:
        return self.cmd("stats")

    def stats_reset(self):
        self.cmd("stats", "reset")

    def events(self) -> list:
        return self.cmd("events")["ev"]

    def reset(self):
        self.cmd("reset", timeout=8)

    def virt(self, on=True):
        self.cmd("virt", 1 if on else 0)

    def press(self, btn: str):
        self.cmd("press", btn)

    def release(self, btn: str):
        self.cmd("release", btn)

    def click(self, btn: str, long=False):
        """Короткое/долгое нажатие через реальный код опроса кнопок (debounce 30 мс, долгое 600 мс)."""
        self.press(btn)
        time.sleep(0.75 if long else 0.09)
        self.release(btn)
        time.sleep(0.12)

    def combo(self, hold: str, btn: str, long=False):
        """Удерживать `hold`, нажать `btn`, затем отпустить `hold` (порядок как у человека)."""
        self.press(hold)
        time.sleep(0.09)
        self.press(btn)
        time.sleep(0.75 if long else 0.09)
        self.release(btn)
        time.sleep(0.09)
        self.release(hold)
        time.sleep(0.12)

    def enc(self, n: int):
        self.cmd("enc", n)
        time.sleep(0.08)

    def app(self, n: int):
        self.cmd("app", n)

    def param(self, label: str, value: int | None = None) -> int | None:
        r = self.cmd("param", label, *( [] if value is None else [value]))
        return r["v"]

    def screen(self) -> Screen:
        return Screen(bytes.fromhex(self.cmd("screen")["hex"]))

    def eeprom(self, start=0, length=48) -> bytes:
        return bytes.fromhex(self.cmd("eeprom", start, length)["hex"])

    def flushsave(self):
        self.cmd("flushsave")

    def load(self, which: str, slot0: int):
        return self.cmd("load", which, slot0)["ok"]

    def seqdump(self, start=0, count=16) -> list:
        return self.cmd("seqdump", start, count)["steps"]

    def songdump(self) -> list:
        return self.cmd("songdump")["steps"]

    # --- файловая система
    def fs_ls(self) -> dict:
        return {p: n for p, n in self.cmd("fs", "ls")["files"]}

    def fs_get(self, path: str) -> bytes:
        out, off = b"", 0
        while True:
            try:
                r = self.cmd("fs", "get", path, off, 320)
            except DeviceError:
                r = self.cmd("fs", "get", path, off, 320)   # чтение идемпотентно — один повтор
            chunk = bytes.fromhex(r["hex"])
            out += chunk
            off += len(chunk)
            if not chunk or off >= r["size"]:
                return out

    def fs_put(self, path: str, data: bytes):
        assert data, "fs_put: пустой файл не поддерживается"
        off = 0
        while off < len(data):
            chunk = data[off:off + 120]
            self.cmd("fs", "put", path, off, chunk.hex())
            off += len(chunk)

    def fs_rm(self, path: str):
        self.cmd("fs", "rm", path)

    # --- перезагрузка
    def reboot(self, factory=False, wait=20.0):
        try:
            self.cmd("factory" if factory else "reboot", timeout=5)
        except DeviceError:
            pass
        self.close()
        time.sleep(1.0)
        deadline = time.time() + wait
        last = None
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            # Пока USB заново определяется, перечисление портов Windows (SetupAPI) может
            # уронить процесс — поэтому первые попытки открываем тот же COM-порт по имени.
            port = self.port if attempt < 12 else (find_serial_port() or self.port)
            try:
                self.port = port
                self.open()
                self.handshake()
                self.virt(True)
                return
            except Exception as e:  # порт ещё не появился / не отвечает
                last = e
                self.close()
                time.sleep(0.5)
        raise DeviceError(f"устройство не вернулось после перезагрузки: {last}")
