$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $PSScriptRoot 'build'
cmake -S $PSScriptRoot -B $build -A x64
cmake --build $build --config Release
$dll = Join-Path $build 'bin\quicksdf_core.dll'
$target = Join-Path $root 'quick_sdf_blender\bin\quicksdf_core.dll'
if (Test-Path $dll) {
    # Move-Item -Force does not replace an existing DLL reliably on Windows.
    # Keep the CMake artifact and publish a byte-for-byte copy instead.
    Copy-Item -Force -LiteralPath $dll -Destination $target
}
Write-Host "Built $target"
