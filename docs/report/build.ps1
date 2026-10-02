# Сборка PDF отчёта. Шрифты PT Serif Pro в репозиторий не входят —
# укажите папку параметром или переменной ESPIDI_FONTS.
param(
  [string]$Fonts = $(if ($env:ESPIDI_FONTS) { $env:ESPIDI_FONTS } else { "C:\Users\zemuro\Antigravity\Microsound book translation project\scratch\fonts_isolated" })
)
$here = $PSScriptRoot
typst compile --font-path $Fonts (Join-Path $here "ESPidi_test_report.typ") (Join-Path $here "ESPidi_test_report.pdf")
