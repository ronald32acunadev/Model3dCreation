$ErrorActionPreference = 'Stop'

$repo = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $repo

if (-not (Get-Command uv -ErrorAction SilentlyContinue) -and -not (Test-Path "$env:USERPROFILE\.local\bin\uv.exe")) {
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
}
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    Write-Host "Missing: NVIDIA driver (nvidia-smi). Download it from https://www.nvidia.com/drivers"
}
if (-not (Get-Command nvcc -ErrorAction SilentlyContinue)) {
    Write-Host "Missing: CUDA 13 toolkit (nvcc). Download it from https://developer.nvidia.com/cuda-downloads"
}
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$hasBuildTools = [bool](Get-Command cl.exe -ErrorAction SilentlyContinue)
if (-not $hasBuildTools -and (Test-Path $vswhere)) {
    $hasBuildTools = [bool](& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath)
}
if (-not $hasBuildTools) {
    Write-Host "Missing: Visual Studio Build Tools. Download them from https://visualstudio.microsoft.com/visual-cpp-build-tools/"
}

if (Test-Path 'client\.venv\bin') {
    Write-Host 'client\.venv is a Linux environment. This script will not delete it: move or remove it by hand, then run this script again.'
    exit 2
}

if ($args -contains '--dry-run' -and (Test-Path 'client\.venv\Scripts\python.exe')) {
    Write-Host 'Dry run: client environment exists, skipping the dependency sync.'
} else {
    uv sync --project client --frozen --no-dev --inexact
}
client\.venv\Scripts\python.exe scripts\setup\setup_repo.py @args
exit $LASTEXITCODE
