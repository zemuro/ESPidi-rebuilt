// Тестовая обвязка: USB-консоль управления и метрики. См. test_hooks.h.
// Компилируется только при -DESPIDI_TEST (окружение PlatformIO `test`).
#ifdef ESPIDI_TEST

#include "test_hooks.h"
#undef digitalRead  // внутри обвязки нужен настоящий digitalRead

#include <stdarg.h>
#include <EEPROM.h>
#include <LittleFS.h>
#include "config.h"
#include "hardware.h"
#include "app.h"
#include "arp.h"
#include "seq_mel.h"
#include "seq_song.h"
#include "settings.h"
#include "midi_handler.h"
#include "midi_monitor.h"
#include "clock_engine.h"
#include "inputs.h"
#include "ui.h"
#include "engine.h"

extern Arpeggiator arp;
extern MelodicSequencer melSeq;
extern SongSequencer songSeq;
extern MidiMonitor monitor;
extern SettingsApp settingsApp;
extern bool needsSave;
extern unsigned long lastChangeTime;
extern bool stepEditMode;
extern uint8_t lastEditStep;
extern bool lastEditStepValid;
void saveAllSettings();

// ---------------------------------------------------------------- метрики

struct Hist {
    uint32_t n = 0, max = 0;
    uint64_t sum = 0;
    uint32_t b[10] = {0};
};
static const uint32_t EDGES_MS[] = {500, 1000, 2000, 5000, 10000, 20000, 50000, 100000};  // мкс
static const uint32_t EDGES_LATE[] = {100, 500, 1000, 2000, 5000, 10000, 20000};          // мкс

static void histAdd(Hist& h, uint32_t v, const uint32_t* edges, int ne) {
    h.n++;
    h.sum += v;
    if (v > h.max) h.max = v;
    int i = 0;
    while (i < ne && v >= edges[i]) i++;
    h.b[i]++;
}

struct ScopeStat {
    const char* name = nullptr;
    uint32_t n = 0, max = 0;
    uint64_t sum = 0;
};
static ScopeStat s_scopes[16];

struct Ev {
    uint32_t t;
    const char* kind;
    uint32_t v;
};
static Ev s_ev[64];
static uint8_t s_evHead = 0, s_evCount = 0;

static Hist s_loop, s_gap, s_late;
static uint32_t s_commits = 0;
static uint32_t s_lastLoop = 0, s_lastSvc = 0;

// Метрики пишут две задачи (движок и основной цикл) — правки под короткой критической секцией.
static portMUX_TYPE s_statMux = portMUX_INITIALIZER_UNLOCKED;

static void evPush(const char* kind, uint32_t v) {
    portENTER_CRITICAL(&s_statMux);
    s_ev[s_evHead] = {micros(), kind, v};
    s_evHead = (s_evHead + 1) % 64;
    if (s_evCount < 64) s_evCount++;
    portEXIT_CRITICAL(&s_statMux);
}

static void statsReset() {
    portENTER_CRITICAL(&s_statMux);
    s_loop = Hist();
    s_gap = Hist();
    s_late = Hist();
    s_commits = 0;
    s_lastLoop = 0;
    s_lastSvc = 0;
    s_evHead = s_evCount = 0;
    for (auto& s : s_scopes) s = ScopeStat();
    portEXIT_CRITICAL(&s_statMux);
}

void th_loopBegin() {
    uint32_t now = micros();
    if (s_lastLoop) {
        uint32_t dt = now - s_lastLoop;
        histAdd(s_loop, dt, EDGES_MS, 8);
        if (dt > 8000) evPush("loop", dt);
    }
    s_lastLoop = now;
}

void th_serviceMark() {
    uint32_t now = micros();
    if (s_lastSvc) {
        uint32_t dt = now - s_lastSvc;
        histAdd(s_gap, dt, EDGES_MS, 8);
        if (dt > 8000) evPush("gap", dt);
    }
    s_lastSvc = now;
}

void th_tickLate(uint32_t lateUs) {
    histAdd(s_late, lateUs, EDGES_LATE, 7);
    if (lateUs > 3000) evPush("late", lateUs);
}

void th_countCommit() { s_commits++; }

// ---------------------------------------------------------------- журнал входящих MIDI-байтов

struct RxEntry {
    uint32_t t;
    uint8_t b;
};
static RxEntry s_rx[512];
static uint16_t s_rxHead = 0, s_rxCount = 0;
static uint32_t s_rxTotal = 0;

struct MidiEv {
    uint32_t t;
    char kind;
    uint32_t v;
};
static MidiEv s_mev[32];
static uint8_t s_mevHead = 0, s_mevCount = 0;

void th_logRx(uint8_t b) {
    s_rx[s_rxHead] = {micros(), b};
    s_rxHead = (s_rxHead + 1) % 512;
    if (s_rxCount < 512) s_rxCount++;
    s_rxTotal++;
}

void th_logMidiEvent(char kind, uint32_t value) {
    s_mev[s_mevHead] = {micros(), kind, value};
    s_mevHead = (s_mevHead + 1) % 32;
    if (s_mevCount < 32) s_mevCount++;
}

static void onMidiError(int8_t err) { th_logMidiEvent('E', (uint8_t)err); }

void th_scopeRecord(const char* name, uint32_t us) {
    portENTER_CRITICAL(&s_statMux);
    ScopeStat* slot = nullptr;
    for (auto& s : s_scopes) {
        if (s.name == name || (s.name && strcmp(s.name, name) == 0)) { slot = &s; break; }
        if (!s.name && !slot) slot = &s;
    }
    if (slot) {
        if (!slot->name) slot->name = name;
        slot->n++;
        slot->sum += us;
        if (us > slot->max) slot->max = us;
    }
    portEXIT_CRITICAL(&s_statMux);
    if (slot && us > 5000) evPush(name, us);
}

// ---------------------------------------------------------------- виртуальные кнопки

static volatile uint8_t s_ovMask = 0;  // бит = номер GPIO, для которого подменяем чтение
static volatile uint8_t s_ovLow = 0;   // бит = 1 → «нажата» (LOW)

int IRAM_ATTR th_digitalRead(uint8_t pin) {
    if (pin < 8 && ((s_ovMask >> pin) & 1)) return ((s_ovLow >> pin) & 1) ? 0 : 1;
    return digitalRead(pin);
}

static int pinByName(const char* n) {
    if (!strcmp(n, "enc")) return ENC_BTN;
    if (!strcmp(n, "lr")) return BTN_L_R;
    if (!strcmp(n, "tap")) return BTN_TAP;
    if (!strcmp(n, "play")) return BTN_PLAY;
    return -1;
}

// ---------------------------------------------------------------- вывод

static void jf(String& s, const char* fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    s += buf;
}

// Команды выполняются под блокировкой движка, а ответ уходит в USB уже после неё (sendReplies):
// передача пары килобайт занимает десятки миллисекунд и не должна задерживать такты.
static String s_out;

static void reply(uint32_t id, const String& json) {
    s_out.reserve(s_out.length() + json.length() + 16);
    s_out += '@';
    s_out += String(id);
    s_out += ' ';
    s_out += json;
    s_out += '\n';
}

static void sendReplies() {
    if (!s_out.length()) return;
    Serial.print(s_out);
    Serial.flush();  // HWCDC держит неполный 64-байтный пакет в FIFO — выталкиваем
    s_out = String();
}

static void replyErr(uint32_t id, const char* msg) {
    String s = "{\"err\":\"";
    s += msg;
    s += "\"}";
    reply(id, s);
}

static void histJson(String& s, const char* key, const Hist& h, int nb) {
    jf(s, "\"%s\":{\"n\":%u,\"max\":%u,\"avg\":%u,\"b\":[", key, (unsigned)h.n, (unsigned)h.max,
       (unsigned)(h.n ? h.sum / h.n : 0));
    for (int i = 0; i < nb; i++) jf(s, "%s%u", i ? "," : "", (unsigned)h.b[i]);
    s += "]}";
}

// ---------------------------------------------------------------- команды

static void cmdState(uint32_t id) {
    String s;
    s.reserve(1800);
    jf(s, "{\"t\":%u,\"app\":%d,\"warn\":%d,\"ui\":%d,\"col\":%d,\"row\":%d,\"bpm\":%u,\"heap\":%u,\"minheap\":%u,",
       (unsigned)micros(), (int)currentAppType, (int)ui_unsavedWarnActive(), (int)uiState, (int)currentCol, cursorVisualRow, (unsigned)globalBpm,
       (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap());
    jf(s, "\"clk\":{\"ext\":%d,\"out\":%d,\"tr\":%d,\"alive\":%d,\"disp\":%u,\"ticks\":%u},",
       clock_isSourceExternal(), clock_isOutEnabled(), clock_isTransportEnabled(), clock_isAlive(),
       (unsigned)clock_getDisplayBpm(), (unsigned)clock_getTickCount());
    jf(s, "\"arp\":{\"en\":%d,\"mode\":%u,\"div\":%u,\"gate\":%u,\"swing\":%u,\"strum\":%u,\"hold\":%u,"
          "\"ch\":%u,\"oct\":%u,\"held\":%u,\"holdn\":%u,\"playing\":%u},",
       arp.params.enabled, arp.params.mode, arp.params.division, arp.params.gate, arp.params.swing,
       arp.params.strum, arp.params.hold, arp.params.channel, arp.params.octaves, arp.getHeldCount(),
       arp.getHoldNoteCount(), arp.getPlayingNote());
    jf(s, "\"mel\":{\"en\":%d,\"rec\":%u,\"step\":%u,\"edit\":%u,\"len\":%u,\"mode\":%u,\"strum\":%u,"
          "\"rerec\":%u,\"ch\":%u,\"gate\":%u,\"swing\":%u,\"rand\":%u,\"prob\":%u,\"page\":%u,"
          "\"ptrn\":%u,\"dirty\":%d,\"stepedit\":%d,\"tredit\":%d,\"swap\":%u,\"swpend\":%d,\"last\":%u},",
       melSeq.enabled, melSeq.recording, melSeq.getCurrentStep(), melSeq.getEditStep(), melSeq.params.length,
       melSeq.params.mode, melSeq.params.strum, melSeq.params.reRec, melSeq.params.channel, melSeq.params.gate,
       melSeq.params.swing, melSeq.params.randomness, melSeq.params.probability, melSeq.params.page,
       melSeq.getCurrentPattern(), melSeq.isDirty(), melSeq.stepEditActive, melSeq.transposeEditActive,
       settingsApp.params.ptrnSwitch, melSeq.isSwitchPending(), melSeq.getLastPlayedStep());
    jf(s, "\"song\":{\"en\":%d,\"step\":%u,\"edit\":%u,\"len\":%u,\"mode\":%u,\"cycle\":%u,\"ch\":%u,"
          "\"song\":%u,\"dirty\":%d,\"stepedit\":%d,\"sel\":%d},",
       songSeq.enabled, songSeq.getCurrentStep(), songSeq.getEditStep(), songSeq.params.length,
       songSeq.params.mode, songSeq.params.cycle, songSeq.params.channel, songSeq.getCurrentSong(),
       songSeq.isDirty(), songSeq.stepEditActive, songSeq.stepSelectMode);
    jf(s, "\"set\":{\"clkin\":%u,\"clkout\":%u,\"start\":%u,\"bright\":%u},\"save\":{\"pending\":%d},",
       settingsApp.params.clockIn, settingsApp.params.clockOut, settingsApp.params.start,
       settingsApp.params.brightness, needsSave);
    s += "\"mon\":{\"notes\":[";
    for (int i = 0; i < monitor.getActiveNoteCount(); i++) {
        const NoteEvent& n = monitor.getActiveNotes()[i];
        jf(s, "%s[%u,%u,%u,%d]", i ? "," : "", n.channel, n.note, n.velocity, n.active);
    }
    s += "],\"ccs\":[";
    for (int i = 0; i < monitor.getActiveCCCount(); i++) {
        const CCEvent& c = monitor.getActiveCCs()[i];
        jf(s, "%s[%u,%u,%d]", i ? "," : "", c.number, c.value, c.dirty);
    }
    s += "]}}";
    reply(id, s);
}

static void cmdStats(uint32_t id) {
    String s;
    s.reserve(1500);
    s += "{";
    histJson(s, "loop", s_loop, 9);
    s += ",";
    histJson(s, "gap", s_gap, 9);
    s += ",";
    histJson(s, "late", s_late, 8);
    jf(s, ",\"commits\":%u,\"scopes\":{", (unsigned)s_commits);
    bool first = true;
    for (auto& sc : s_scopes) {
        if (!sc.name) continue;
        jf(s, "%s\"%s\":{\"n\":%u,\"max\":%u,\"sum\":%u}", first ? "" : ",", sc.name, (unsigned)sc.n,
           (unsigned)sc.max, (unsigned)sc.sum);
        first = false;
    }
    s += "}}";
    reply(id, s);
}

static void cmdEvents(uint32_t id) {
    String s = "{\"ev\":[";
    uint8_t start = (s_evHead + 64 - s_evCount) % 64;
    for (uint8_t i = 0; i < s_evCount; i++) {
        const Ev& e = s_ev[(start + i) % 64];
        jf(s, "%s[%u,\"%s\",%u]", i ? "," : "", (unsigned)e.t, e.kind, (unsigned)e.v);
    }
    s += "]}";
    s_evHead = s_evCount = 0;
    reply(id, s);
}

static void doReset() {
    if (arp.params.hold) {
        arp.params.hold = 0;
        arp.clearHold();
    }
    while (arp.getHeldCount()) arp.handleNoteOff(arp.getHeldNotes()[0], 1);
    arp.stop();
    melSeq.stop();
    songSeq.stop();
    melSeq.recording = 0;
    melSeq.stepEditActive = false;
    melSeq.transposeEditActive = false;
    songSeq.stepEditActive = false;
    songSeq.stepSelectMode = false;
    stepEditMode = false;
    lastEditStep = 0;
    lastEditStepValid = false;
    melSeq.setCurrentStep(0);
    melSeq.setEditStepDirect(0);
    songSeq.setCurrentStep(0);
    songSeq.setEditStepDirect(0);
    arp.params = ArpParams();
    melSeq.params = MelSeqParams();
    songSeq.params = SongParams();
    settingsApp.params = SettingsParams();
    clock_setSourceExternal(false);
    clock_setOutEnabled(false);
    clock_setTransportEnabled(false);
    clock_setBpm(120);
    melSeq.clear();
    songSeq.clear();
    melSeq.markClean();  // сброс стенда — чистый лист, а не несохранённые правки
    songSeq.markClean();
    melSeq.markClean();
    songSeq.markClean();
    needsSave = false;
    monitor.begin();
    ui_setApp(APP_ARPEGGIATOR);
    settingsApp.applyBrightness();
    statsReset();
}

static bool findParam(const char* label, ParamDef** out, int* col, int* idx) {
    ParamDef* defs[2] = {leftParams, rightParams};
    int counts[2] = {leftParamCount, rightParamCount};
    for (int c = 0; c < 2; c++) {
        if (!defs[c]) continue;
        for (int i = 0; i < counts[c]; i++) {
            if (!strcmp(defs[c][i].label, label)) {
                *out = &defs[c][i];
                *col = c;
                *idx = i;
                return true;
            }
        }
    }
    return false;
}

static void cmdParam(uint32_t id, const char* label, const char* valueStr) {
    ParamDef* p;
    int col, idx;
    if (!findParam(label, &p, &col, &idx)) return replyErr(id, "no such param in current app");
    stepEditMode = false;
    melSeq.stepEditActive = false;
    songSeq.stepEditActive = false;
    uiState = UI_NAVIGATE;
    currentCol = col ? COL_RIGHT : COL_LEFT;
    int scroll = idx >= VISIBLE_ROWS ? idx - VISIBLE_ROWS + 1 : 0;
    (col ? rightScrollOffset : leftScrollOffset) = scroll;
    cursorVisualRow = idx - scroll;
    if (!valueStr) {
        ui_handleEncoderPress(true);  // SAVE / HELP
        uiState = UI_NAVIGATE;
    } else {
        if (!p->value) return replyErr(id, "param has no value");
        int delta = atoi(valueStr) - (int)*p->value;
        uiState = UI_EDIT;
        if (delta) ui_handleEncoder(delta);
        uiState = UI_NAVIGATE;
    }
    ui_markDirty(UI_DIRTY_FULL);
    String s = "{\"v\":";
    s += p->value ? String((int)*p->value) : String("null");
    s += "}";
    reply(id, s);
}

static void ensureDirs() {
    if (!LittleFS.exists(PATTERN_DIR)) LittleFS.mkdir(PATTERN_DIR);
    if (!LittleFS.exists(SONG_DIR)) LittleFS.mkdir(SONG_DIR);
}

static void cmdFs(uint32_t id, char* a1, char* a2, char* a3, char* a4) {
    if (!a1) return replyErr(id, "fs: subcommand");
    if (!strcmp(a1, "ls")) {
        String s = "{\"files\":[";
        bool first = true;
        const char* dirs[2] = {PATTERN_DIR, SONG_DIR};
        for (auto d : dirs) {
            File dir = LittleFS.open(d);
            if (!dir || !dir.isDirectory()) continue;
            File f = dir.openNextFile();
            while (f) {
                jf(s, "%s[\"%s/%s\",%u]", first ? "" : ",", d, f.name(), (unsigned)f.size());
                first = false;
                f = dir.openNextFile();
            }
        }
        s += "]}";
        return reply(id, s);
    }
    if (!strcmp(a1, "get") && a2) {  // fs get <path> <off> <len>
        File f = LittleFS.open(a2, "r");
        if (!f) return replyErr(id, "no file");
        uint32_t off = a3 ? atoi(a3) : 0, len = a4 ? atoi(a4) : 256;
        if (len > 384) len = 384;
        f.seek(off);
        String s = "{\"size\":" + String((unsigned)f.size()) + ",\"hex\":\"";
        uint8_t b;
        char h[3];
        for (uint32_t i = 0; i < len && f.read(&b, 1) == 1; i++) {
            snprintf(h, sizeof(h), "%02x", b);
            s += h;
        }
        s += "\"}";
        return reply(id, s);
    }
    if (!strcmp(a1, "put") && a2 && a3 && a4) {  // fs put <path> <off> <hex>
        ensureDirs();
        // Запись по смещению (а не дописывание) — повтор той же команды безопасен
        uint32_t off = atoi(a3);
        File f = LittleFS.open(a2, off == 0 ? "w" : "r+");
        if (!f) return replyErr(id, "open failed");
        if (off) f.seek(off);
        size_t n = strlen(a4) / 2;
        for (size_t i = 0; i < n; i++) {
            char h[3] = {a4[2 * i], a4[2 * i + 1], 0};
            f.write((uint8_t)strtol(h, nullptr, 16));
        }
        f.close();
        songSeq.patternChanged(0);
        return reply(id, "{\"ok\":1}");
    }
    if (!strcmp(a1, "rm") && a2) {
        LittleFS.remove(a2);
        songSeq.patternChanged(0);
        return reply(id, "{\"ok\":1}");
    }
    if (!strcmp(a1, "format")) {
        LittleFS.format();
        LittleFS.begin(true);
        songSeq.patternChanged(0);
        return reply(id, "{\"ok\":1}");
    }
    replyErr(id, "fs: unknown");
}

static void cmdSeqDump(uint32_t id, int start, int count) {
    String s = "{\"steps\":[";
    for (int st = start; st < start + count && st < MAX_SEQ_STEPS; st++) {
        jf(s, "%s{\"n\":[", st > start ? "," : "");
        for (int i = 0; i < MAX_POLY; i++) {
            int8_t n = melSeq.getNoteAt(st, i);
            if (n < 0) break;
            jf(s, "%s%d", i ? "," : "", n);
        }
        s += "],\"v\":[";
        for (int i = 0; i < MAX_POLY; i++) {
            if (melSeq.getNoteAt(st, i) < 0) break;
            jf(s, "%s%u", i ? "," : "", melSeq.getVelocityAt(st, i));
        }
        s += "],\"cc\":[";
        for (int i = 0; i < melSeq.getCCCount(st); i++)
            jf(s, "%s[%u,%u]", i ? "," : "", melSeq.getCCNumberAt(st, i), melSeq.getCCValueAt(st, i));
        jf(s, "],\"tie\":%d,\"tr\":%d}", melSeq.getTie(st), melSeq.getTranspose(st));
    }
    s += "]}";
    reply(id, s);
}

static void cmdSongDump(uint32_t id) {
    String s = "{\"steps\":[";
    for (int st = 0; st < MAX_SONG_STEPS; st++) {
        SongStepParams& p = songSeq.getStepParams(st);
        jf(s, "%s[%u,%d,%u,%u,%d]", st ? "," : "", p.patternSlot, p.transpose, p.divider, p.pauseLength, p.mute);
    }
    s += "]}";
    reply(id, s);
}

static uint32_t s_floodUntil = 0;  // millis(): до какого момента забивать MIDI OUT байтами Clock

static void execLine(char* line) {
    char* tok[8] = {nullptr};
    int n = 0;
    char* p = line;
    while (*p && n < 8) {
        while (*p == ' ') *p++ = 0;
        if (!*p) break;
        tok[n++] = p;
        while (*p && *p != ' ') p++;
    }
    if (n < 2) return;
    uint32_t id = (uint32_t)atoi(tok[0]);
    const char* cmd = tok[1];
    char* a1 = n > 2 ? tok[2] : nullptr;
    char* a2 = n > 3 ? tok[3] : nullptr;
    char* a3 = n > 4 ? tok[4] : nullptr;
    char* a4 = n > 5 ? tok[5] : nullptr;

    if (!strcmp(cmd, "ping")) {
        String s = "{\"t\":" + String((unsigned)micros()) + ",\"rst\":" + String((int)esp_reset_reason()) +
                   ",\"fw\":\"test\",\"build\":\"" __DATE__ " " __TIME__ "\"}";
        return reply(id, s);
    }
    if (!strcmp(cmd, "state")) return cmdState(id);
    if (!strcmp(cmd, "stats")) {
        if (a1 && !strcmp(a1, "reset")) {
            statsReset();
            return reply(id, "{\"ok\":1}");
        }
        return cmdStats(id);
    }
    if (!strcmp(cmd, "events")) return cmdEvents(id);
    if (!strcmp(cmd, "reset")) {
        doReset();
        return reply(id, "{\"ok\":1}");
    }
    if (!strcmp(cmd, "virt")) {  // virt 1|0 — включить/выключить подмену кнопок
        if (a1 && atoi(a1)) {
            s_ovLow = 0;
            s_ovMask = (1 << ENC_BTN) | (1 << BTN_L_R) | (1 << BTN_TAP) | (1 << BTN_PLAY);
        } else {
            s_ovMask = 0;
        }
        return reply(id, "{\"ok\":1}");
    }
    if (!strcmp(cmd, "press") || !strcmp(cmd, "release")) {
        int pin = a1 ? pinByName(a1) : -1;
        if (pin < 0) return replyErr(id, "button: enc|lr|tap|play");
        if (!strcmp(cmd, "press")) s_ovLow |= (1 << pin);
        else s_ovLow &= ~(1 << pin);
        return reply(id, "{\"ok\":1}");
    }
    if (!strcmp(cmd, "enc")) {
        encDelta = encDelta + (a1 ? atoi(a1) : 1);
        return reply(id, "{\"ok\":1}");
    }
    if (!strcmp(cmd, "app")) {
        int a = a1 ? atoi(a1) : 0;
        if (a < 0 || a >= APP_COUNT) return replyErr(id, "app 0..4");
        ui_setApp((AppType)a);
        return reply(id, "{\"ok\":1}");
    }
    if (!strcmp(cmd, "param") && a1) return cmdParam(id, a1, a2);
    if (!strcmp(cmd, "screen")) {
        String s = "{\"hex\":\"";
        const uint8_t* b = display.getBuffer();
        char h[3];
        for (int i = 0; i < OLED_WIDTH * OLED_HEIGHT / 8; i++) {
            snprintf(h, sizeof(h), "%02x", b[i]);
            s += h;
        }
        s += "\"}";
        return reply(id, s);
    }
    if (!strcmp(cmd, "eeprom")) {
        int start = a1 ? atoi(a1) : 0, len = a2 ? atoi(a2) : 48;
        String s = "{\"hex\":\"";
        char h[3];
        for (int i = start; i < start + len && i < 512; i++) {
            snprintf(h, sizeof(h), "%02x", EEPROM.read(i));
            s += h;
        }
        s += "\"}";
        return reply(id, s);
    }
    if (!strcmp(cmd, "flushsave")) {  // не ждать 5 с: считать, что таймер сохранения истёк
        if (needsSave) lastChangeTime = millis() - SAVE_DELAY_MS - 1;
        return reply(id, "{\"ok\":1}");
    }
    if (!strcmp(cmd, "rxlog")) {  // rxlog [clear]: последние принятые байты MIDI IN и события парсера
        if (a1 && !strcmp(a1, "clear")) {
            s_rxHead = s_rxCount = 0;
            s_rxTotal = 0;
            s_mevHead = s_mevCount = 0;
            return reply(id, "{\"ok\":1}");
        }
        uint16_t n = s_rxCount > 200 ? 200 : s_rxCount;  // ответ ≤ ~2 КБ
        uint16_t start = (s_rxHead + 512 - n) % 512;
        String s;
        s.reserve(2400);
        jf(s, "{\"total\":%u,\"hex\":\"", (unsigned)s_rxTotal);
        char h[3];
        for (uint16_t i = 0; i < n; i++) {
            snprintf(h, sizeof(h), "%02x", s_rx[(start + i) % 512].b);
            s += h;
        }
        s += "\",\"t\":[";
        for (uint16_t i = 0; i < n; i++)
            jf(s, "%s%u", i ? "," : "", (unsigned)s_rx[(start + i) % 512].t);
        s += "],\"ev\":[";
        uint8_t es = (s_mevHead + 32 - s_mevCount) % 32;
        for (uint8_t i = 0; i < s_mevCount; i++) {
            const MidiEv& e = s_mev[(es + i) % 32];
            jf(s, "%s[%u,\"%c\",%u]", i ? "," : "", (unsigned)e.t, e.kind, (unsigned)e.v);
        }
        s += "]}";
        return reply(id, s);
    }
    if (!strcmp(cmd, "txflood")) {  // txflood <сек>: непрерывный поток 0xF8 на MIDI OUT (проверка выхода мультиметром)
        s_floodUntil = millis() + 1000UL * (a1 ? atoi(a1) : 30);
        return reply(id, "{\"ok\":1}");
    }
    if (!strcmp(cmd, "fs")) return cmdFs(id, a1, a2, a3, a4);
    if (!strcmp(cmd, "load")) {  // load mel|song <slot0>
        if (!a1 || !a2) return replyErr(id, "load mel|song <slot0>");
        bool ok;
        if (!strcmp(a1, "mel")) {
            ok = melSeq.loadFromFile(atoi(a2));
            if (currentAppType == APP_MEL_SEQ) ui_setApp(APP_MEL_SEQ);
        } else {
            ok = songSeq.loadFromFile(atoi(a2));
            if (currentAppType == APP_SONG) ui_setApp(APP_SONG);
        }
        return reply(id, ok ? "{\"ok\":1}" : "{\"ok\":0}");
    }
    if (!strcmp(cmd, "seqdump")) return cmdSeqDump(id, a1 ? atoi(a1) : 0, a2 ? atoi(a2) : 16);
    if (!strcmp(cmd, "songdump")) return cmdSongDump(id);
    if (!strcmp(cmd, "factory") || !strcmp(cmd, "reboot")) {
        if (!strcmp(cmd, "factory")) {
            for (int i = 0; i < 128; i++) EEPROM.write(i, 0xFF);
            EEPROM.commit();
            LittleFS.format();
        }
        reply(id, "{\"ok\":1}");
        sendReplies();
        delay(150);
        ESP.restart();
        return;
    }
    replyErr(id, "unknown command");
}

// ---------------------------------------------------------------- жизненный цикл

void th_setup() {
    Serial.setTxBufferSize(4096);
    Serial.setRxBufferSize(2048);  // длинные строки `fs put` не должны переполнять приём
    Serial.begin(115200);
    Serial.setTxTimeoutMs(30);  // не подвешивать цикл, если порт никто не читает
    statsReset();
}

void th_poll() {
    static bool handlersSet = false;
    if (!handlersSet) {  // после midi_setup(): прошивка этот обработчик не использует
        MIDI.setHandleError(onMidiError);
        handlersSet = true;
    }
    if (s_floodUntil) {
        if ((int32_t)(millis() - s_floodUntil) < 0) {
            EngineLock lock;
            for (int i = 0; i < 8; i++) MIDI.sendRealTime(midi::Clock);
        } else {
            s_floodUntil = 0;
        }
    }
    static char line[700];
    static size_t len = 0;
    for (int guard = 0; guard < 256 && Serial.available(); guard++) {
        int c = Serial.read();
        if (c == '\n' || c == '\r') {
            if (len) {
                line[len] = 0;
                {
                    EngineLock lock;
                    execLine(line);
                }
                sendReplies();
                len = 0;
            }
        } else if (len < sizeof(line) - 1) {
            line[len++] = (char)c;
        } else {
            len = 0;  // переполнение — отбрасываем строку
        }
    }
}

#endif  // ESPIDI_TEST
