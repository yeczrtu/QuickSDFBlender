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
$previewRenderSmokeTest = Join-Path $repositoryRoot 'tests\blender_preview_render_probe.py'
$schemaSmokeTest = Join-Path $repositoryRoot 'tests\blender_schema6_smoke.py'
$interactivePaintSmokeTest = Join-Path $repositoryRoot 'tests\blender_interactive_paint_smoke.py'
$studioSmokeTest = Join-Path $repositoryRoot 'tests\blender_studio_smoke.py'
$studioSwitchSmokeTest = Join-Path $repositoryRoot 'tests\blender_studio_switch_smoke.py'
$autoKeySmokeTest = Join-Path $repositoryRoot 'tests\blender_auto_key_smoke.py'
$projectionPaintSmokeTest = Join-Path $repositoryRoot 'tests\blender_projection_paint_smoke.py'
$icospherePaintSmokeTest = Join-Path $repositoryRoot 'tests\blender_icosphere_paint_smoke.py'
$savedStateSmokeTest = Join-Path $repositoryRoot 'tests\blender_saved_state_smoke.py'
$installedSmokeTest = Join-Path $repositoryRoot 'tests\blender_installed_extension_smoke.py'
$archiveVerification = Join-Path $repositoryRoot 'tests\verify_extension_archive.py'
$extensionSource = Join-Path $repositoryRoot 'quick_sdf_blender'
$manifestPath = Join-Path $extensionSource 'blender_manifest.toml'
$manifestText = Get-Content -Raw -LiteralPath $manifestPath
if ($manifestText -notmatch '(?m)^version\s*=\s*"([^"]+)"\s*$') {
    throw "Could not read the extension version from $manifestPath"
}
$extensionVersion = $Matches[1]
$extensionArchive = Join-Path $buildDirectory "quick_sdf_blender-$extensionVersion-windows-x64.zip"
$studioResult = Join-Path $buildDirectory 'studio_smoke_result.txt'
$studioSavedBlend = Join-Path $buildDirectory 'studio_adjusted_save.blend'
$studioSwitchResult = Join-Path $buildDirectory 'studio_switch_smoke_result.txt'
$autoKeyResult = Join-Path $buildDirectory 'auto_key_smoke_result.txt'
$projectionPaintResult = Join-Path $buildDirectory 'projection_paint_smoke_result.txt'
$icospherePaintResult = Join-Path $buildDirectory 'icosphere_paint_smoke_result.txt'

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

    Write-Host '==> 1/15 Build Windows native core'
    $global:LASTEXITCODE = 0
    & $nativeBuild
    if ($LASTEXITCODE -ne 0) {
        throw "Native build failed with exit code $LASTEXITCODE"
    }
    $nativeLibrary = Join-Path $extensionSource 'bin\quicksdf_core.dll'
    if (-not (Test-Path -LiteralPath $nativeLibrary -PathType Leaf)) {
        throw "Native build did not produce $nativeLibrary"
    }

    Write-Host '==> 2/15 Run Python unit tests'
    & $PythonPath -m unittest discover -s tests -p 'test_*.py'
    if ($LASTEXITCODE -ne 0) {
        throw "Unit tests failed with exit code $LASTEXITCODE"
    }

    Write-Host '==> 3/15 Run Blender 5.1 background smoke test'
    & $BlenderPath --background --factory-startup --python-exit-code 1 `
        --python $smokeTest -- --output-dir $buildDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Blender smoke test failed with exit code $LASTEXITCODE"
    }

    Write-Host '==> 4/15 Verify active Material Output and Canvas preview rendering'
    & $BlenderPath --background --factory-startup --python-exit-code 1 `
        --python $previewRenderSmokeTest
    if ($LASTEXITCODE -ne 0) {
        throw "Blender preview render smoke test failed with exit code $LASTEXITCODE"
    }

    Write-Host '==> 5/15 Verify schema-6 bitplane save/reload persistence'
    & $BlenderPath --background --factory-startup --python-exit-code 1 `
        --python $schemaSmokeTest
    if ($LASTEXITCODE -ne 0) {
        throw "Blender schema-6 smoke test failed with exit code $LASTEXITCODE"
    }

    Write-Host '==> 6/15 Verify typed Display/Coverage Smart Paint in background mode'
    & $BlenderPath --background --factory-startup --python-exit-code 1 `
        --python $interactivePaintSmokeTest
    if ($LASTEXITCODE -ne 0) {
        throw "Blender interactive paint smoke test failed with exit code $LASTEXITCODE"
    }

    Write-Host '==> 7/15 Run Blender 5.1 interactive Studio lifecycle smoke test'
    if (Test-Path -LiteralPath $studioResult) {
        Remove-Item -Force -LiteralPath $studioResult
    }
    & $BlenderPath --enable-event-simulate --factory-startup --python-exit-code 1 `
        --python $studioSmokeTest
    if ($LASTEXITCODE -ne 0) {
        throw "Blender Studio smoke test failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath $studioResult -PathType Leaf)) {
        throw 'Blender Studio smoke test did not produce a result file'
    }
    $studioOutcome = (Get-Content -Raw -LiteralPath $studioResult).Trim()
    if ($studioOutcome -ne 'PASS') {
        throw "Blender Studio smoke test failed:`n$studioOutcome"
    }
    if (-not (Test-Path -LiteralPath $studioSavedBlend -PathType Leaf)) {
        throw 'Blender Studio smoke test did not produce its active-session save'
    }

    Write-Host '==> 8/15 Verify one-click Studio model switching'
    if (Test-Path -LiteralPath $studioSwitchResult) {
        Remove-Item -Force -LiteralPath $studioSwitchResult
    }
    & $BlenderPath --factory-startup --python-exit-code 1 --python $studioSwitchSmokeTest
    if ($LASTEXITCODE -ne 0) {
        throw "Blender Studio switch smoke test failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath $studioSwitchResult -PathType Leaf)) {
        throw 'Blender Studio switch smoke test did not produce a result file'
    }
    $studioSwitchOutcome = (Get-Content -Raw -LiteralPath $studioSwitchResult).Trim()
    if ($studioSwitchOutcome -ne 'PASS') {
        throw "Blender Studio switch smoke test failed:`n$studioSwitchOutcome"
    }

    Write-Host '==> 9/15 Verify adaptive angle-key creation and transactional Undo/Redo'
    if (Test-Path -LiteralPath $autoKeyResult) {
        Remove-Item -Force -LiteralPath $autoKeyResult
    }
    & $BlenderPath --factory-startup --python-exit-code 1 --python $autoKeySmokeTest
    if ($LASTEXITCODE -ne 0) {
        throw "Blender auto-key smoke test failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath $autoKeyResult -PathType Leaf)) {
        throw 'Blender auto-key smoke test did not produce a result file'
    }
    $autoKeyOutcome = (Get-Content -Raw -LiteralPath $autoKeyResult).Trim()
    if ($autoKeyOutcome -ne 'PASS') {
        throw "Blender auto-key smoke test failed:`n$autoKeyOutcome"
    }

    Write-Host '==> 10/15 Run a native 3D Projection Paint stroke through Quick SDF'
    if (Test-Path -LiteralPath $projectionPaintResult) {
        Remove-Item -Force -LiteralPath $projectionPaintResult
    }
    & $BlenderPath --factory-startup --python-exit-code 1 --python $projectionPaintSmokeTest
    if ($LASTEXITCODE -ne 0) {
        throw "Blender Projection Paint smoke test failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath $projectionPaintResult -PathType Leaf)) {
        throw 'Blender Projection Paint smoke test did not produce a result file'
    }
    $projectionPaintOutcome = (Get-Content -Raw -LiteralPath $projectionPaintResult).Trim()
    if ($projectionPaintOutcome -ne 'PASS') {
        throw "Blender Projection Paint smoke test failed:`n$projectionPaintOutcome"
    }

    Write-Host '==> 11/15 Verify repeated artist painting on a Normal Guide Icosphere'
    if (Test-Path -LiteralPath $icospherePaintResult) {
        Remove-Item -Force -LiteralPath $icospherePaintResult
    }
    & $BlenderPath --enable-event-simulate --factory-startup --python-exit-code 1 `
        --python $icospherePaintSmokeTest
    if ($LASTEXITCODE -ne 0) {
        throw "Blender Icosphere paint smoke test failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath $icospherePaintResult -PathType Leaf)) {
        throw 'Blender Icosphere paint smoke test did not produce a result file'
    }
    $icospherePaintOutcome = (Get-Content -Raw -LiteralPath $icospherePaintResult).Trim()
    if ($icospherePaintOutcome -ne 'PASS') {
        throw "Blender Icosphere paint smoke test failed:`n$icospherePaintOutcome"
    }

    Write-Host '==> 12/15 Verify active Studio save in a fresh Blender process'
    & $BlenderPath --background --factory-startup --python-exit-code 1 `
        --python $savedStateSmokeTest `
        -- --blend $studioSavedBlend
    if ($LASTEXITCODE -ne 0) {
        throw "Blender saved-state smoke test failed with exit code $LASTEXITCODE"
    }

    Write-Host "==> 13/15 Build and validate Blender extension $extensionVersion"
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

    Write-Host '==> 14/15 Verify release ZIP contents byte-for-byte'
    & $PythonPath $archiveVerification `
        --archive $extensionArchive `
        --source $extensionSource `
        --expected-version $extensionVersion
    if ($LASTEXITCODE -ne 0) {
        throw "Extension archive verification failed with exit code $LASTEXITCODE"
    }

    Write-Host '==> 15/15 Install and exercise the ZIP in an isolated Blender user directory'
    $isolatedUser = Join-Path $buildDirectory ("isolated-user-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $isolatedUser | Out-Null
    $previousUserResources = $env:BLENDER_USER_RESOURCES
    try {
        $env:PYTHONPATH = $null
        $env:BLENDER_USER_RESOURCES = $isolatedUser
        & $BlenderPath --background --factory-startup --command extension install-file `
            -r user_default -e $extensionArchive
        if ($LASTEXITCODE -ne 0) {
            throw "Isolated extension install failed with exit code $LASTEXITCODE"
        }
        & $BlenderPath --background --python-exit-code 1 --python $installedSmokeTest `
            -- --expected-version $extensionVersion --isolated-root $isolatedUser
        if ($LASTEXITCODE -ne 0) {
            throw "Installed extension smoke test failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        $env:BLENDER_USER_RESOURCES = $previousUserResources
        $env:PYTHONPATH = if ($previousPythonPath) {
            "$repositoryRoot;$previousPythonPath"
        } else {
            $repositoryRoot
        }
    }

    $archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $extensionArchive).Hash.ToLowerInvariant()
    Write-Host "Build complete: $extensionArchive"
    Write-Host "SHA256: $archiveHash"
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
