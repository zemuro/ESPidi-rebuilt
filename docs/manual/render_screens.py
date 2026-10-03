"""Рисует кадры экрана ESPidi (screens/raw.json от capture_screens.py) как настоящий OLED 128×32:
белые пиксели на чёрном стекле, тонкая сетка между пикселями, лёгкое свечение.

    python render_screens.py
"""
import json
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "screens", "raw.json")
W, H = 128, 32
CELL = 6            # шаг пикселя на картинке
DOT = 5             # светящаяся часть пикселя (остальное — зазор сетки)
PAD = 18            # чёрное стекло вокруг активной области
BEZEL = 10          # рамка модуля
LIT = (238, 242, 255)
GLASS = (6, 7, 9)
FRAME = (34, 36, 40)


def pixels(raw: bytes):
    for y in range(H):
        for x in range(W):
            if raw[x + (y // 8) * W] >> (y % 8) & 1:
                yield x, y


def render(raw: bytes) -> Image.Image:
    gw, gh = W * CELL + 2 * PAD, H * CELL + 2 * PAD
    lit = Image.new("RGB", (gw, gh), (0, 0, 0))
    dr = ImageDraw.Draw(lit)
    on = set(pixels(raw))
    seam = tuple(int(v * 0.72) for v in LIT)  # зазор между соседними горящими пикселями: притушен,
    for x, y in on:                            # а не чёрный — иначе текст на выделении распадается
        x0, y0 = PAD + x * CELL, PAD + y * CELL
        dr.rectangle([x0, y0, x0 + DOT - 1, y0 + DOT - 1], fill=LIT)
        if (x + 1, y) in on:
            dr.rectangle([x0 + DOT, y0, x0 + CELL - 1, y0 + DOT - 1], fill=seam)
        if (x, y + 1) in on:
            dr.rectangle([x0, y0 + DOT, x0 + DOT - 1, y0 + CELL - 1], fill=seam)
        if (x + 1, y) in on and (x, y + 1) in on and (x + 1, y + 1) in on:
            dr.rectangle([x0 + DOT, y0 + DOT, x0 + CELL - 1, y0 + CELL - 1], fill=seam)
    glow = lit.filter(ImageFilter.GaussianBlur(4)).point(lambda v: int(v * 0.55))
    glass = Image.new("RGB", (gw, gh), GLASS)
    screen = ImageChops.add(ImageChops.add(glass, glow), lit)

    img = Image.new("RGB", (gw + 2 * BEZEL, gh + 2 * BEZEL), (255, 255, 255))
    frame = Image.new("L", img.size, 0)
    ImageDraw.Draw(frame).rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], radius=14, fill=255)
    img.paste(Image.new("RGB", img.size, FRAME), (0, 0), frame)
    mask = Image.new("L", (gw, gh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, gw - 1, gh - 1], radius=8, fill=255)
    img.paste(screen, (BEZEL, BEZEL), mask)
    return img


def main():
    shots = json.load(open(SRC, encoding="utf-8"))
    for name, hx in shots.items():
        render(bytes.fromhex(hx)).save(os.path.join(HERE, "screens", name + ".png"), optimize=True)
    print(len(shots), "frames")


if __name__ == "__main__":
    main()
