# Запуск тестов ESPidi: .\run_tests.ps1 [аргументы pytest]
#   .\run_tests.ps1 -m unit                 # проверки инфраструктуры без устройства
#   .\run_tests.ps1 --flash                 # собрать/залить окружение `test`, затем прогнать всё
#   .\run_tests.ps1 -k "b2 or b4" --long    # выбранные тесты, длинные прогоны
#   .\run_tests.ps1 --allow-wipe            # включая тесты со стиранием flash
$env:PYTHONUTF8 = "1"
$here = $PSScriptRoot
$py = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  py -3.12 -m venv (Join-Path $here ".venv")
  & $py -m pip install -q -r (Join-Path $here "requirements.txt")
}
Push-Location $here
& $py -m pytest @args
Pop-Location
