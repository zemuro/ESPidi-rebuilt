"""Графики и сводные цифры для отчёта по таймингу (ESPidi_timing_report.typ).

Вход: runs/<этап>_<прогон>.json — results.json полных прогонов автотестов (tools/results/<прогон>/)
для четырёх версий прошивки. Выход: figs/*.png и data.json.

    python make_figs.py [папка шрифтов PT Serif Pro]
"""
import glob
import json
import os
import re
import sys

import matplotlib

matplotlib.use("agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from matplotlib.colors import LogNorm
from matplotlib.lines import Line2D

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "ESPIDI_FONTS", r"C:\Users\zemuro\Antigravity\Microsound book translation project\scratch\fonts_isolated")
OUT = os.path.join(HERE, "figs")
os.makedirs(OUT, exist_ok=True)

# Нужны TTF: OTF с контурами CFF matplotlib рисует неверно (см. otf2ttf.py)
for f in glob.glob(os.path.join(FONTS, "*.ttf")):
    fm.fontManager.addfont(f)
plt.rcParams.update({
    "font.family": "PTSerifPro-Book",
    "font.size": 8.5,
    "axes.edgecolor": "#555555",
    "axes.linewidth": 0.6,
    "axes.labelcolor": "#1b1b1b",
    "xtick.color": "#444444",
    "ytick.color": "#444444",
    "xtick.major.width": 0.5,
    "ytick.major.width": 0.5,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "legend.frameon": False,
})
INK, QUIET, ACCENT = "#1b1b1b", "#5f5f5f", "#7a2e1d"

STAGES = [
    ("s0", "Исходная", "#7a2e1d", "o"),
    ("s1", "Волна 1", "#d08a4c", "s"),
    ("s2", "Волна 2.1", "#8c9bb0", "D"),
    ("s3", "Волна 2.2", "#6f9e8f", "v"),
    ("s4", "Волна 2.3", "#1f4e46", "o"),
]
SCEN = [
    ("B1", "Ничего не играет"),
    ("B2", "Арпеджиатор + экран"),
    ("B3", "Вращение энкодера"),
    ("B4", "Шаги секвенсора"),
    ("B5", "Стык паттернов в песне"),
    ("B6", "Отпускание аккорда ARP"),
    ("B7", "Смена PTRN во время игры"),
    ("B8", "Автосохранение"),
]
LATE_BUCKETS = ["<0.1мс", "<0.5мс", "<1мс", "<2мс", "<5мс", "<10мс", "<20мс", "≥20мс"]
LATE_LABELS = ["<0,1", "0,1–0,5", "0,5–1", "1–2", "2–5", "5–10", "10–20", "≥20"]
NOMINAL = 60000.0 / (120 * 24)
LIMIT = 26.0


def load():
    data = {}
    for key, *_ in STAGES:
        f = glob.glob(os.path.join(HERE, "runs", key + "_*.json"))[0]
        for x in json.load(open(f, encoding="utf-8")):
            data.setdefault((key, x["id"]), x)
    return data


R = load()


def props(k, tid):
    x = R.get((k, tid))
    return {p[0]: p[1] for p in x["props"]} if x else {}


def pc(k, tid):
    s = props(k, tid).get("ПК: Clock")
    m = re.search(r"σ=([\d.]+) min=([\d.]+) max=([\d.]+).*выбросов>[\d.]+мс: (\d+)", s or "")
    return dict(zip(("sd", "min", "max", "over"), map(float, m.groups()))) if m else None


def late_hist(k, tid):
    s = props(k, tid).get("опоздание тика, гист.", "")
    h = [0] * len(LATE_BUCKETS)
    for part in s.split():
        lab, _, n = part.rpartition(":")
        if lab in LATE_BUCKETS:
            h[LATE_BUCKETS.index(lab)] = int(n)
    return h


def scopes(k, tid):
    return props(k, tid).get("scopes (макс, мкс)", {}) or {}


# ---------- сводка для отчёта
S = {}
for k, *_ in STAGES:
    for tid, _ in SCEN:
        p = pc(k, tid)
        if not p:
            continue
        h = late_hist(k, tid)
        worst = max((i for i, n in enumerate(h) if n), default=0)
        S[f"{k}/{tid}"] = dict(p, over=int(p["over"]), late_hist=h, late_worst=LATE_LABELS[worst],
                               late_over1=sum(h[3:]), scopes=scopes(k, tid))
with open(os.path.join(HERE, "data.json"), "w", encoding="utf-8") as fh:
    json.dump(S, fh, ensure_ascii=False, indent=1)


def save(fig, name):
    fig.savefig(os.path.join(OUT, name), dpi=300, bbox_inches="tight", pad_inches=0.04)
    plt.close(fig)


def comma(v, _=None):
    return f"{v:g}".replace(".", ",")


def stage_legend(ax, hollow_note=False):
    h = [Line2D([], [], marker=m, ls="", color=c, markersize=5, label=l) for _, l, c, m in STAGES]
    if hollow_note:
        h.append(Line2D([], [], marker="o", ls="", markerfacecolor="white", markeredgecolor=STAGES[-1][2],
                        markersize=5, label="в фоне, такты\nне задерживает"))
    ax.legend(handles=h, loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=7.5,
              handletextpad=0.2, borderaxespad=0.2)


def dotplot(name, rows, values, xlabel, refs=(), xlim=None, hollow=None, ticks=None):
    """Строка на сценарий/операцию, точка на версию; ось X логарифмическая."""
    n = len(rows)
    fig, ax = plt.subplots(figsize=(4.5, 0.4 * n + 0.75))
    for i, (key, label) in enumerate(rows):
        y = n - 1 - i
        xs = [values.get((k, key)) for k, *_ in STAGES]
        known = [x for x in xs if x is not None]
        if known:
            ax.plot([min(known), max(known)], [y, y], color="#d9d9d9", lw=1.8, zorder=1, solid_capstyle="round")
        for j, ((k, _, c, m), x) in enumerate(zip(STAGES, xs)):
            if x is None:
                continue
            face = "white" if hollow and hollow(k, key) else c
            ax.plot(x, y + (j - (len(STAGES) - 1) / 2) * 0.09, marker=m, color=c, markerfacecolor=face, markersize=4.8, ls="", zorder=3)
    for r, (lab, v) in enumerate(refs):
        ax.axvline(v, color=QUIET, lw=0.6, ls=(0, (3, 2)), zorder=0)
        ha = "center" if len(refs) == 1 else ("right" if r == 0 else "left")
        ax.text(v * (0.97 if ha == "right" else 1.03 if ha == "left" else 1.0), n - 0.35, lab,
                fontsize=7, color=QUIET, ha=ha, va="bottom")
    ax.set_yticks(range(n))
    ax.set_yticklabels([l for _, l in rows][::-1])
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    ax.set_xscale("log")
    if xlim:
        ax.set_xlim(*xlim)
    ax.set_ylim(-0.6, n - 0.1)
    ax.set_xlabel(xlabel)
    if ticks:
        ax.set_xticks(ticks)
        ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(comma))
    stage_legend(ax, hollow_note=bool(hollow))
    save(fig, name)


# 1. Наибольший интервал Clock на ПК
dotplot("max_interval.png", SCEN,
        {(k, t): S[f"{k}/{t}"]["max"] for k, *_ in STAGES for t, _ in SCEN if f"{k}/{t}" in S},
        "наибольший интервал между Clock, мс (лог. шкала)",
        refs=(("норма", NOMINAL), ("допуск", LIMIT)), xlim=(15, 200), ticks=[20, 30, 50, 100, 150])

# 2. Разброс интервала
dotplot("sigma.png", SCEN,
        {(k, t): S[f"{k}/{t}"]["sd"] for k, *_ in STAGES for t, _ in SCEN if f"{k}/{t}" in S},
        "стандартное отклонение интервала Clock, мс (лог. шкала)", xlim=(0.04, 20))


# 3. Диапазон интервалов: мин–макс полосами
def ranges():
    n = len(SCEN)
    fig, ax = plt.subplots(figsize=(4.5, 0.52 * n + 0.75))
    for i, (t, label) in enumerate(SCEN):
        y = n - 1 - i
        for j, (k, _, c, _) in enumerate(STAGES):
            r = S.get(f"{k}/{t}")
            if not r:
                continue
            yy = y + ((len(STAGES) - 1) / 2 - j) * 0.15
            ax.plot([r["min"], r["max"]], [yy, yy], color=c, lw=2.4, solid_capstyle="butt")
    ax.axvspan(0.1, NOMINAL, color="#f3f3f3", zorder=0, lw=0)
    ax.axvline(NOMINAL, color=QUIET, lw=0.6, ls=(0, (3, 2)))
    ax.axvline(LIMIT, color=ACCENT, lw=0.6, ls=(0, (1, 2)))
    ax.text(NOMINAL * 0.96, n - 0.3, "норма", fontsize=7, color=QUIET, ha="right", va="bottom")
    ax.text(LIMIT * 1.04, n - 0.3, "допуск", fontsize=7, color=ACCENT, ha="left", va="bottom")
    ax.set_xscale("log")
    ax.set_xlim(0.12, 200)
    ax.set_ylim(-0.6, n - 0.05)
    ax.set_yticks(range(n))
    ax.set_yticklabels([l for _, l in SCEN][::-1])
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    ax.set_xlabel("интервал между Clock: от наименьшего до наибольшего, мс (лог. шкала)")
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(comma))
    h = [Line2D([], [], color=c, lw=2.4, label=l) for _, l, c, _ in STAGES]
    ax.legend(handles=h, loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=7.5)
    save(fig, "ranges.png")


ranges()


# 4. Опоздание тактов: тепловая карта по корзинам, панель на версию
def late_heat():
    fig, axes = plt.subplots(1, len(STAGES), figsize=(7.4, 2.9), sharey=True)
    norm = LogNorm(vmin=1, vmax=600)
    cmap = matplotlib.colormaps["YlOrBr"]
    for ax, (k, label, c, _) in zip(axes, STAGES):
        grid = [late_hist(k, t) for t, _ in SCEN]
        masked = [[v if v else float("nan") for v in row] for row in grid]
        ax.imshow(masked, aspect="auto", cmap=cmap, norm=norm, interpolation="nearest")
        for y, row in enumerate(grid):
            for x, v in enumerate(row):
                if v:
                    ax.text(x, y, str(v), ha="center", va="center", fontsize=5.6,
                            color="white" if v > 120 else INK)
        ax.set_xticks(range(len(LATE_LABELS)))
        ax.set_xticklabels(LATE_LABELS, rotation=90, fontsize=6.5)
        ax.set_title(label, fontsize=8, color=c)
        ax.axvline(2.5, color=ACCENT, lw=0.6, ls=(0, (1, 2)))
        for s in ax.spines.values():
            s.set_visible(False)
        ax.tick_params(length=0)
    axes[0].set_yticks(range(len(SCEN)))
    axes[0].set_yticklabels([l for _, l in SCEN])
    fig.text(0.62, -0.06, "опоздание такта по часам ESP32, мс", ha="center")
    fig.subplots_adjust(wspace=0.06)
    save(fig, "late_heat.png")


late_heat()

# 5. Долгие операции: максимум по всем B-тестам
OPS = [
    ("dispatch_tick", "Обработка одного такта"),
    ("song.stopAll", "Остановка нот песни"),
    ("arp.stopSounding", "Отпускание аккорда ARP"),
    ("mel.stopAll", "Гашение нот шага SEQ"),
    ("ui.draw", "Кадр экрана¹"),
    ("ui.flush", "Отправка кадра по I²C"),
    ("mel.load", "Чтение паттерна (PTRN)"),
    ("song.loadBuf", "Подгрузка паттерна песни"),
    ("save.eeprom", "Запись настроек во флеш"),
]
BACKGROUND_S3 = {"ui.flush", "mel.load", "song.loadBuf"}

OPV = {}
for k, *_ in STAGES:
    for op, _ in OPS:
        v = [scopes(k, t).get(op) for t, _ in SCEN]
        v = [x for x in v if x]
        if v:
            OPV[(k, op)] = max(v) / 1000.0
with open(os.path.join(HERE, "data.json"), "w", encoding="utf-8") as fh:
    json.dump(dict(S, ops={f"{k}/{op}": v for (k, op), v in OPV.items()}), fh, ensure_ascii=False, indent=1)

dotplot("blocking.png", OPS, OPV, "наибольшая длительность, мс (лог. шкала)",
        refs=(("допуск 3 мс", 3.0),), xlim=(0.001, 300),
        hollow=lambda k, op: k in ("s3", "s4") and op in BACKGROUND_S3)

print("ok:", len(S), "сценариев×версий;", sorted(os.listdir(OUT)))
