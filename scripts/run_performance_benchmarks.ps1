[CmdletBinding()]
param(
    [string]$BlenderPath = 'C:\Program Files\Blender Foundation\Blender 5.1\blender.exe',
    [string]$OutputDirectory = '',
    [switch]$FullMatrix
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$probe = Join-Path $root 'benchmarks\blender_performance_probe.py'
if (-not (Test-Path -LiteralPath $BlenderPath -PathType Leaf)) {
    throw "Blender executable was not found: $BlenderPath"
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $root 'build\performance'
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$cases = if ($FullMatrix) {
    foreach ($resolution in 512, 1024, 2048, 4096) {
        foreach ($keys in 8, 16, 32) {
            foreach ($laneMode in 'MIRROR', 'INDEPENDENT') {
                [pscustomobject]@{ Resolution = $resolution; Keys = $keys; LaneMode = $laneMode }
            }
        }
    }
} else {
    @([pscustomobject]@{ Resolution = 2048; Keys = 16; LaneMode = 'MIRROR' })
}

foreach ($case in $cases) {
    if ($case.LaneMode -eq 'MIRROR' -and $case.Keys -gt 16) {
        continue
    }
    $stem = "r$($case.Resolution)-k$($case.Keys)-$($case.LaneMode.ToLowerInvariant())"
    $probeOutput = Join-Path $OutputDirectory "$stem-probe.json"
    $samplesOutput = Join-Path $OutputDirectory "$stem-memory.csv"
    $arguments = @(
        '--background', '--factory-startup', '--python-exit-code', '1',
        '--python', $probe, '--',
        '--repository', $root,
        '--resolution', [string]$case.Resolution,
        '--keys', [string]$case.Keys,
        '--lane-mode', $case.LaneMode,
        '--output', $probeOutput
    )
    $process = Start-Process -FilePath $BlenderPath -ArgumentList $arguments -PassThru -WindowStyle Hidden
    $samples = [System.Collections.Generic.List[object]]::new()
    $started = [DateTimeOffset]::UtcNow
    while (-not $process.HasExited) {
        try {
            $process.Refresh()
            $samples.Add([pscustomobject]@{
                elapsed_ms = [math]::Round(([DateTimeOffset]::UtcNow - $started).TotalMilliseconds, 3)
                working_set_bytes = [int64]$process.WorkingSet64
                private_bytes = [int64]$process.PrivateMemorySize64
                peak_working_set_bytes = [int64]$process.PeakWorkingSet64
                peak_virtual_bytes = [int64]$process.PeakVirtualMemorySize64
            })
        } catch [System.InvalidOperationException] {
            break
        }
        Start-Sleep -Milliseconds 50
    }
    $process.WaitForExit()
    $samples | Export-Csv -LiteralPath $samplesOutput -NoTypeInformation -Encoding UTF8
    if ($process.ExitCode -ne 0) {
        throw "Performance probe $stem failed with exit code $($process.ExitCode)"
    }
    if (-not (Test-Path -LiteralPath $probeOutput -PathType Leaf)) {
        throw "Performance probe $stem did not produce JSON output"
    }
    Write-Host "Measured $stem -> $probeOutput"
}
