[CmdletBinding()]
param(
    [string]$BlenderPath = 'C:\Program Files\Blender Foundation\Blender 5.1\blender.exe',
    [string]$PythonPath = 'python'
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$buildDirectory = Join-Path $repositoryRoot 'build'
$nativeBuild = Join-Path $repositoryRoot 'native\build.ps1'
$smokeTest = Join-Path $repositoryRoot 'tests\blender_smoke.py'
$migrationSmokeTest = Join-Path $repositoryRoot 'tests\blender_migration_smoke.py'
$extensionSource = Join-Path $repositoryRoot 'quick_sdf_blender'
$extensionArchive = Join-Path $buildDirectory 'quick_sdf_blender.zip'

if (-not (Test-Path -LiteralPath $BlenderPath -PathType Leaf)) {
    throw "Blender executable was not found: $BlenderPath"
}
if (-not (Get-Command $PythonPath -ErrorAction SilentlyContinue)) {
    throw "Python executable was not found: $PythonPath"
}
New-Item -ItemType Directory -Force -Path $buildDirectory | Out-Null

Push-Location $repositoryRoot
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = if ($previousPythonPath) {
        "$repositoryRoot;$previousPythonPath"
    } else {
        $repositoryRoot
    }

    Write-Host '==> 1/5 Build Windows native core'
    $global:LASTEXITCODE = 0
    & $nativeBuild
    if ($LASTEXITCODE -ne 0) {
        throw "Native build failed with exit code $LASTEXITCODE"
    }
    $nativeLibrary = Join-Path $extensionSource 'bin\quicksdf_core.dll'
    if (-not (Test-Path -LiteralPath $nativeLibrary -PathType Leaf)) {
        throw "Native build did not produce $nativeLibrary"
    }

    Write-Host '==> 2/5 Run Python unit tests'
    & $PythonPath -m unittest discover -s tests -p 'test_*.py'
    if ($LASTEXITCODE -ne 0) {
        throw "Unit tests failed with exit code $LASTEXITCODE"
    }

    Write-Host '==> 3/5 Run Blender 5.1 background smoke test'
    & $BlenderPath --background --factory-startup --python $smokeTest -- --output-dir $buildDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Blender smoke test failed with exit code $LASTEXITCODE"
    }

    Write-Host '==> 4/5 Verify schema v1 to v2 save/reload migration'
    & $BlenderPath --background --factory-startup --python $migrationSmokeTest
    if ($LASTEXITCODE -ne 0) {
        throw "Blender migration smoke test failed with exit code $LASTEXITCODE"
    }

    Write-Host '==> 5/5 Build Blender extension archive'
    if (Test-Path -LiteralPath $extensionArchive) {
        Remove-Item -Force -LiteralPath $extensionArchive
    }
    & $BlenderPath --background --factory-startup --command extension build `
        --source-dir $extensionSource `
        --output-filepath $extensionArchive
    if ($LASTEXITCODE -ne 0) {
        throw "Extension build failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath $extensionArchive -PathType Leaf)) {
        throw "Extension build did not produce $extensionArchive"
    }

    & $BlenderPath --background --factory-startup --command extension validate $extensionArchive
    if ($LASTEXITCODE -ne 0) {
        throw "Extension validation failed with exit code $LASTEXITCODE"
    }

    Write-Host "Build complete: $extensionArchive"
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
