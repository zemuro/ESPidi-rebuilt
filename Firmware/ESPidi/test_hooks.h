// Тестовая обвязка ESPidi. Активна ТОЛЬКО в окружении PlatformIO `test`
// (-DESPIDI_TEST, подключается через -include). В обычной сборке файл пуст,
// а все вставки в прошивку обёрнуты в #ifdef ESPIDI_TEST — бинарник baseline не меняется.
#ifndef TEST_HOOKS_H
#define TEST_HOOKS_H

#ifdef ESPIDI_TEST

#include <Arduino.h>

void th_setup();                    // USB-консоль, сброс счётчиков
void th_poll();                     // разбор команд консоли (вызывать в loop)
void th_loopBegin();                // начало итерации loop() — замер длительности цикла
void th_serviceMark();              // точка обслуживания (clock_update) — замер зазоров
void th_tickLate(uint32_t lateUs);  // опоздание внутреннего тика относительно расписания
void th_countCommit();              // счётчик EEPROM.commit()
void th_scopeRecord(const char* name, uint32_t us);

struct ThScope {
    const char* name;
    uint32_t t0;
    explicit ThScope(const char* n) : name(n), t0(micros()) {}
    ~ThScope() { th_scopeRecord(name, micros() - t0); }
};
#define TH_SCOPE(n) ThScope _th_scope_(n)

// Журнал входящих MIDI-байтов: MidiSerial (midi_serial.h) передаёт сюда каждый сырой байт с UART.
void th_logRx(uint8_t b);
void th_logMidiEvent(char kind, uint32_t value);  // 'S' — SysEx разобран (длина), 'E' — ошибка парсера


// Виртуальные кнопки: пока override включён, digitalRead() для пинов кнопок
// возвращает заданный уровень — реальный код inputs/ui отрабатывает без изменений.
int IRAM_ATTR th_digitalRead(uint8_t pin);
#define digitalRead(p) th_digitalRead(p)

#endif  // ESPIDI_TEST
#endif  // TEST_HOOKS_H
