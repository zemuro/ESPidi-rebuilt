# ESPidi — прошивка ESPidi_2 с исправлениями

**ESPidi** — открытый MIDI-процессор на ESP32-C3: арпеджиатор, шаговый секвенсор, песни из паттернов, MIDI-монитор и MIDI Clock в корпусе размером с ладонь. Автор устройства и прошивки — Евгений ([EugeneCarlo/ESPidi](https://github.com/EugeneCarlo/ESPidi)).

В этом репозитории — прошивка **ESPidi_2** (рабочая версия автора 29.08) с исправлениями: найденные автотестами на живом устройстве ошибки устранены, MIDI Clock стал ровным при любой нагрузке, добавлены функции, которые согласовал автор. Исправления предложены в основной репозиторий ([PR #1](https://github.com/EugeneCarlo/ESPidi/pull/1), [PR #2](https://github.com/EugeneCarlo/ESPidi/pull/2)); здесь они собраны в готовую к использованию версию.

![ESPidi](Image/photo.jpg)

## Скачать

**[Последний выпуск →](https://github.com/zemuro/ESPidi-rebuilt/releases/latest)**

| Файл | Что это |
|---|---|
| `ESPidi_full_0x0.bin` | Готовая прошивка одним файлом, записывается с адреса `0x0` |
| `ESPidi_2_rebuilt_*.zip` | Полный комплект: прошивка, исходники для Arduino IDE, руководство, отчёт, инструкция |
| `ESPidi_manual.pdf` | Руководство пользователя |
| `ESPidi_timing_report.pdf` | Замеры тайминга до и после исправлений |

### Как прошить

Из браузера (Chrome или Edge): откройте [esptool-js](https://espressif.github.io/esptool-js/), подключитесь к устройству на скорости 460800, укажите адрес `0x0` и файл `ESPidi_full_0x0.bin`, нажмите **Program**.

Из командной строки:

```
esptool.py --chip esp32c3 --port COM5 --baud 460800 write_flash 0x0 ESPidi_full_0x0.bin
```

Если устройство не определяется, удерживайте кнопку BOOT на плате при подключении USB.

**При обновлении с версии 29.08** настройки один раз сбросятся к заводским (в прежней версии блоки настроек в памяти перекрывались); паттерны и песни сохраняются. Параметр STRUM теперь называется THRU; в секвенсоре он по умолчанию выключен — включите его, чтобы слышать свою игру.

## Что изменилось по сравнению с ESPidi_2 (29.08)

**Тайминг.** Раньше на стыке паттернов в песне и при отпускании аккорда в арпеджиаторе MIDI Clock прерывался на 140 мс (норма при 120 BPM — 20,8 мс), и ведомые устройства сбивались. Теперь такты, приём и отправка MIDI работают в отдельной задаче с высоким приоритетом, экран перерисовывает только изменившиеся строки, а во время игры ничего не пишется во флеш.

| Ситуация | Наибольший интервал Clock — было | Стало |
|---|---|---|
| Стык паттернов в песне | 146,6 мс | 21,7 мс |
| Отпускание аккорда в ARP | 139,1 мс | 21,7 мс |
| Шаги секвенсора | 37,2 мс | 21,4 мс |
| Смена паттерна во время игры | 37,2 мс | 23,5 мс |
| Вращение энкодера | 34,7 мс | 23,4 мс |

**Исправлено:** зависшие ноты (ARP с HOLD, смена аккорда, запись в секвенсор, пропавший внешний Clock); вход, «глохнувший» после обрезанного SysEx; двойные ноты в секвенсоре; запись в соседний шаг; неверная длина цепочки Tie; самопроизвольно менявшиеся SWING, RAND и PROB; неверный канал в MONITOR; незапоминавшееся приложение; ошибки песни в режимах RND и MUTE. Остановка гасит только собственные ноты ESPidi, CC 123 больше не рассылается.

**Новое:** режимы смены паттерна во время игры (`SWAP`: NOW / NEXT / END); защита несохранённых правок; GATE хранится в паттерне и учитывается в песне; TAP усредняет три последних интервала; Pitch Bend, Program Change, Aftertouch и SysEx проходят насквозь вместе с нотами; песня в REV стартует с последнего шага; первая нота после PLAY звучит на первой доле.

Полный список с объяснениями — в [руководстве](docs/manual/ESPidi_manual.pdf) и в описаниях [PR #1](https://github.com/EugeneCarlo/ESPidi/pull/1) и [PR #2](https://github.com/EugeneCarlo/ESPidi/pull/2).

## Документация

- [Руководство пользователя](docs/manual/ESPidi_manual.pdf) — от основ MIDI до живого исполнения, с настоящими снимками экрана.
- [Отчёт по таймингу](docs/timing/ESPidi_timing_report.pdf) — пять версий прошивки, восемь сценариев нагрузки, графики.
- [Отчёт о тестировании исходной прошивки](docs/report/ESPidi_test_report.pdf) — 23 найденных дефекта с описанием и замерами.
- [План тестов](docs/TEST_PLAN.md) и [результаты](docs/RESULTS.md).

## Сборка

Основной способ — [PlatformIO](https://platformio.org/): платформа, плата и версии библиотек закреплены в `platformio.ini`.

```
pio run                    # сборка
pio run -t upload          # загрузка по USB
pio run -e test -t upload  # тестовая сборка: USB-консоль и измерения для автотестов
```

Arduino IDE: откройте `Firmware/ESPidi/ESPidi.ino`, плата *ESP32C3 Dev Module* (пакет esp32 2.0.x), библиотеки MIDI Library 5.0.2, Adafruit SSD1306 2.5.17, Adafruit GFX 1.12.6, Adafruit BusIO 1.17.4. Вставки под `#ifdef ESPIDI_TEST` в обычной сборке не компилируются; версия исходников без них — в ветке [`pr/wave2`](https://github.com/zemuro/ESPidi-rebuilt/tree/pr/wave2) и в архиве выпуска.

## Автотесты

Каталог [`tools/`](tools/) — стенд на pytest, который проверяет прошивку на живом устройстве: компьютер играет ноты, отправляет MIDI Clock, нажимает кнопки через USB-консоль тестовой сборки и анализирует всё, что выходит из MIDI OUT, вплоть до интервалов между байтами Clock и снимков экрана. Описание — в [tools/README.md](tools/README.md). Текущее состояние: 86 тестов из 86.

## Устройство

- ESP32-C3 Super Mini
- MIDI IN и MIDI OUT (DIN-5 или TRS 3,5 мм; тип TRS задаётся перемычками)
- OLED 128×32 (SSD1306 0,91″)
- энкодер с кнопкой (EC11) и три кнопки
- встроенный аккумулятор 10440

Схема, печатные платы и перечень компонентов: [Schematic](Schematic/), [PCB](PCB/), [BOM](BOM/BOM.md), назначение выводов — [HARDWARE.MD](HARDWARE.MD).

## Ветки

| Ветка | Содержимое |
|---|---|
| `rebuilt` | Основная: прошивка, автотесты, документация |
| `pr/wave1`, `pr/wave2` | Исправления в виде, предложенном в репозиторий автора (без тестовой обвязки) |
| `import-espidi2` | Исходная прошивка ESPidi_2 без изменений — точка отсчёта |

## Лицензия

GNU General Public License v3.0 — см. [LICENSE](LICENSE). Устройство и исходная прошивка — Евгений ([EugeneCarlo/ESPidi](https://github.com/EugeneCarlo/ESPidi)).

---

## English

**ESPidi** is an open-source ESP32-C3 MIDI processor (arpeggiator, step sequencer, song mode, MIDI monitor, MIDI Clock) by Evgeny ([EugeneCarlo/ESPidi](https://github.com/EugeneCarlo/ESPidi)). This repository contains his ESPidi_2 firmware with fixes found by an automated hardware test rig: MIDI Clock stays steady under any load (worst-case interval 146.6 ms → 23.5 ms at 120 BPM), stuck notes and settings corruption are fixed, and several author-approved features are added. Ready-to-flash binaries are on the [Releases](https://github.com/zemuro/ESPidi-rebuilt/releases/latest) page; documentation is in Russian.
