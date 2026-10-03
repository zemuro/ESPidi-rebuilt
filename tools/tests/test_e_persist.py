"""E. Хранение настроек (EEPROM/NVS) и первый запуск.

Адреса из config.h и жёстко заданные в saveAllSettings расходятся: номер паттерна лежит на байте
SWING секвенсора, а BPM/канал песни — на байтах RAND/PROB. Тесты перезагружают устройство
и сверяют параметры (reboot без стирания flash; factory-тест помечен wipes).
"""
import time

import pytest


def _persist(dut, wait=0.6):
    dut.c.flushsave()
    time.sleep(wait)


@pytest.mark.tid("A3", "P2")
@pytest.mark.wipes
def test_a3_first_boot_defaults(dut):
    """Пустой flash: настройки по умолчанию, LittleFS отформатирован и пуст."""
    dut.c.reboot(factory=True)
    st = dut.state()
    assert st["app"] == 0 and st["bpm"] == 120
    assert st["mel"]["ch"] == 1 and st["mel"]["rand"] == 0 and st["mel"]["prob"] == 0
    assert dut.c.fs_ls() == {}


@pytest.mark.tid("E1", "P0")
@pytest.mark.parametrize("bpm", [120, 140])
def test_e1_eeprom_overlap(dut, rec, bpm):
    """SWING/RAND/PROB секвенсора не должны меняться от PTRN и параметров песни. При BPM>127 блок SEQ отбрасывается."""
    # сначала пустой слот: GATE — свойство паттерна, из файла он перекрыл бы проверяемый
    dut.use("mel", PTRN=60, MODE=2, CH=5, GATE=70, SWING=0, RAND=0, PROB=0)
    dut.use("song", CH=3)
    dut.bpm(bpm)
    _persist(dut)
    dut.c.reboot()
    m = dut.state()["mel"]
    got = dict(mode=m["mode"], ch=m["ch"], gate=m["gate"], swing=m["swing"], rand=m["rand"], prob=m["prob"])
    want = dict(mode=2, ch=5, gate=70, swing=0, rand=0, prob=0)
    rec(f"BPM={bpm}: ожидалось / получено", f"{want} / {got}")
    assert got == want, f"параметры SEQ после перезагрузки изменились: {got}"


@pytest.mark.tid("E3", "P1")
def test_e3_app_selection_persists(dut):
    """Выбранное в меню приложение должно запоминаться (ui_setApp не планирует сохранение)."""
    dut.c.param("GATE", 81)            # гарантированно записать «базу» с ARP
    _persist(dut)
    dut.c.reboot()
    assert dut.state()["app"] == 0
    # Выбор SONG через настоящее меню: долгое нажатие энкодера, поворот, нажатие
    dut.c.click("enc", long=True)
    assert dut.state()["ui"] == 2, "меню не открылось"
    dut.c.enc(2)
    dut.c.click("enc")
    assert dut.state()["app"] == 2
    time.sleep(6.0)                    # пройдёт SAVE_DELAY_MS, если сохранение вообще запланировано
    dut.c.reboot()
    app = dut.state()["app"]
    assert app == 2, f"после перезагрузки приложение {app} вместо выбранного 2"


@pytest.mark.tid("E4", "P1")
def test_e4_arp_play_state_not_restored_as_playing(dut):
    """ArpParams.enabled сохраняется и не проверяется при загрузке: после старта ARP «играет»."""
    dut.use("arp")
    dut.play()
    assert dut.state()["arp"]["en"]
    _persist(dut)
    dut.c.reboot()
    assert not dut.state()["arp"]["en"], "ARP стартует в состоянии PLAY после перезагрузки"


@pytest.mark.tid("E5", "P2")
@pytest.mark.parametrize("app", ["mel", "song"])
def test_e5_plain_play_does_not_change_flash(dut, rec, app):
    """Заявлено: простой PLAY не пишет во flash (EEPROM пишется только при изменении байтов)."""
    dut.use(app)
    dut.c.param("BPM", 121)
    _persist(dut)
    dut.c.param("BPM", 120)
    _persist(dut)
    dut.c.stats_reset()
    before = dut.c.eeprom(0, 48)
    dut.play()
    time.sleep(6.5)                    # сработает отложенное сохранение
    after = dut.c.eeprom(0, 48)
    commits = dut.c.stats()["commits"]
    rec(f"{app}: commit-вызовов / EEPROM изменился", f"{commits} / {before != after}")
    assert before == after, f"байты EEPROM изменились после простого PLAY ({app})"


@pytest.mark.tid("E6", "P1")
def test_e6_swap_setting_persists(dut, rec):
    """Режим смены паттерна (SWAP, хранится с глобальными настройками) переживает перезагрузку."""
    dut.use("mel", SWAP=2)              # END
    _persist(dut)
    dut.c.reboot()
    got = dut.state()["mel"]["swap"]
    rec("SWAP после перезагрузки", got)
    dut.use("mel", SWAP=1)              # вернуть по умолчанию (NEXT)
    _persist(dut)
    assert got == 2
