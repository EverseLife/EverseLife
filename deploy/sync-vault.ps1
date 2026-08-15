# Rebuild the game-design vault and refresh the snapshot in `vault/`.
#
# Run from the game repository root:
#   powershell -File deploy/sync-vault.ps1
#   powershell -File deploy/sync-vault.ps1 -Vault D:\path\to\octoverse-game-design
#
# Numbers are edited only in the vault (D-065). Here -- carrying the build
# output over, so that the server image and CI see the same values as the developer.

[CmdletBinding()]
param(
    # Where the vault lies. Empty -- next to this repository.
    [string]$Vault = '',
    # Do not rebuild, take the ready build/.
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
# The default is computed here, not in the declaration: in Windows PowerShell
# $PSScriptRoot is still empty before the script body.
if (-not $Vault) { $Vault = Join-Path $repo '..\octoverse-game-design' }
$vault = (Resolve-Path $Vault).Path
$build = Join-Path $vault 'build'
$target = Join-Path $repo 'vault'

if (-not (Test-Path $vault)) { throw "vault not found: $vault" }

if (-not $NoBuild) {
    $tool = Join-Path $vault 'tools\build.py'
    if (Test-Path $tool) {
        Write-Host "building the vault: $tool"
        Push-Location $vault
        try { python tools/build.py } finally { Pop-Location }
    } else {
        Write-Warning "tools/build.py not found -- taking the ready build/"
    }
}

if (-not (Test-Path $build)) { throw "no build directory: $build" }

New-Item -ItemType Directory -Force -Path $target | Out-Null
foreach ($name in 'constants.json', 'laws.json', 'plants.json', 'recipes.json') {
    $from = Join-Path $build $name
    if (-not (Test-Path $from)) { throw "the build is incomplete, missing file: $from" }
    Copy-Item $from (Join-Path $target $name) -Force
    Write-Host "  $name"
}

Write-Host ''
Write-Host "snapshot refreshed: $target"
Write-Host 'next: git add vault && git commit && git push -- the numbers reach the server by deploy'
