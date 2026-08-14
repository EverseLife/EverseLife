# Пересобрать вольт гейм-дизайна и обновить слепок в `vault/`.
#
# Запуск из корня репозитория игры:
#   powershell -File deploy/sync-vault.ps1
#   powershell -File deploy/sync-vault.ps1 -Vault D:\path\to\octoverse-game-design
#
# Числа правятся только в вольте (D-065). Здесь — перенос вывода сборки, чтобы
# образ сервера и CI видели те же значения, что и разработчик.

[CmdletBinding()]
param(
    # Где лежит вольт. Пусто — рядом с этим репозиторием.
    [string]$Vault = '',
    # Не пересобирать, взять готовый build/.
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
# Значение по умолчанию считается здесь, а не в объявлении: в Windows
# PowerShell $PSScriptRoot до тела скрипта ещё пуст.
if (-not $Vault) { $Vault = Join-Path $repo '..\octoverse-game-design' }
$vault = (Resolve-Path $Vault).Path
$build = Join-Path $vault 'build'
$target = Join-Path $repo 'vault'

if (-not (Test-Path $vault)) { throw "вольт не найден: $vault" }

if (-not $NoBuild) {
    $tool = Join-Path $vault 'tools\build.py'
    if (Test-Path $tool) {
        Write-Host "сборка вольта: $tool"
        Push-Location $vault
        try { python tools/build.py } finally { Pop-Location }
    } else {
        Write-Warning "tools/build.py не найден — беру готовый build/"
    }
}

if (-not (Test-Path $build)) { throw "нет каталога сборки: $build" }

New-Item -ItemType Directory -Force -Path $target | Out-Null
foreach ($name in 'constants.json', 'laws.json', 'plants.json', 'recipes.json') {
    $from = Join-Path $build $name
    if (-not (Test-Path $from)) { throw "сборка неполна, нет файла: $from" }
    Copy-Item $from (Join-Path $target $name) -Force
    Write-Host "  $name"
}

Write-Host ''
Write-Host "слепок обновлён: $target"
Write-Host 'дальше: git add vault && git commit && git push — числа доедут до сервера деплоем'
