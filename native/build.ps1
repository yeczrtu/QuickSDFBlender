$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $PSScriptRoot 'build'
cmake -S $PSScriptRoot -B $build -A x64
cmake --build $build --config Release
$dll = Join-Path $root 'quick_sdf_blender\bin\Release\quicksdf_core.dll'
$target = Join-Path $root 'quick_sdf_blender\bin\quicksdf_core.dll'
if (Test-Path $dll) {
    Move-Item -Force -LiteralPath $dll -Destination $target
}
Write-Host "Built $target"
