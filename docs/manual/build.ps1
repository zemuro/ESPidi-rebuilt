# Сборка PDF-мануала. Шрифты PT Serif Pro в репозиторий не входят —
# укажите папку с ними параметром или переменной ESPIDI_FONTS.
param(
  [string]$Fonts = $(if ($env:ESPIDI_FONTS) { $env:ESPIDI_FONTS } else { "C:\Users\zemuro\Antigravity\Microsound book translation project\scratch\fonts_isolated" })
)
$here = $PSScriptRoot
typst compile --root (Join-Path $here "..\..") --font-path $Fonts (Join-Path $here "ESPidi_manual.typ") (Join-Path $here "ESPidi_manual.pdf")
