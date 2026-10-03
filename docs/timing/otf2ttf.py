"""PT Serif Pro (OTF, контуры CFF) → TTF для matplotlib: CFF-шрифты он рисует неверно.

    python otf2ttf.py <папка с .otf> <папка для .ttf>
"""
import glob
import os
import sys

from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable


def convert(src, dst):
    font = TTFont(src)
    glyph_order = font.getGlyphOrder()
    gs = font.getGlyphSet()
    glyf = newTable("glyf")
    glyf.glyphOrder = glyph_order
    glyf.glyphs = {}
    for name in glyph_order:
        pen = TTGlyphPen(gs)
        gs[name].draw(Cu2QuPen(pen, 1.0, reverse_direction=True))
        glyf[name] = pen.glyph()
    font["glyf"] = glyf
    font["loca"] = newTable("loca")
    maxp = font["maxp"]
    maxp.tableVersion = 0x00010000
    for attr in ("maxZones", "maxTwilightPoints", "maxStorage", "maxFunctionDefs", "maxInstructionDefs",
                 "maxStackElements", "maxSizeOfInstructions", "maxComponentElements"):
        setattr(maxp, attr, 0)
    maxp.maxZones = 1
    font["head"].glyphDataFormat = 0
    font["post"].formatType = 2.0
    font["post"].extraNames = []
    font["post"].mapping = {}
    for t in ("CFF ", "VORG"):
        if t in font:
            del font[t]
    font.sfntVersion = "\x00\x01\x00\x00"
    font.save(dst)


if __name__ == "__main__":
    os.makedirs(sys.argv[2], exist_ok=True)
    for f in glob.glob(os.path.join(sys.argv[1], "*.otf")):
        out = os.path.join(sys.argv[2], os.path.splitext(os.path.basename(f))[0] + ".ttf")
        convert(f, out)
        print(out)
