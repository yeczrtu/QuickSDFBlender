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

function Get-QuickSdfStage {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return 'startup'
    }
    try {
        # The Blender probe replaces this tiny file while the sampler is
        # running.  A read can therefore observe the truncate/write gap.
        $value = [string](Get-Content -Raw -LiteralPath $Path -ErrorAction Stop)
        if ([string]::IsNullOrWhiteSpace($value)) {
            return 'startup'
        }
        return $value.Trim()
    }
    catch {
        return 'startup'
    }
}

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
    $stageOutput = Join-Path $OutputDirectory "$stem-stage.txt"
    $summaryOutput = Join-Path $OutputDirectory "$stem-summary.json"
    if (Test-Path -LiteralPath $stageOutput) {
        Remove-Item -Force -LiteralPath $stageOutput
    }
    $arguments = @(
        '--background', '--factory-startup', '--python-exit-code', '1',
        '--python', $probe, '--',
        '--repository', $root,
        '--resolution', [string]$case.Resolution,
        '--keys', [string]$case.Keys,
        '--lane-mode', $case.LaneMode,
        '--output', $probeOutput,
        '--stage-file', $stageOutput
    )
    $process = Start-Process -FilePath $BlenderPath -ArgumentList $arguments -PassThru -WindowStyle Hidden
    $samples = [System.Collections.Generic.List[object]]::new()
    $started = [DateTimeOffset]::UtcNow
    while (-not $process.HasExited) {
        try {
            $process.Refresh()
            $samples.Add([pscustomobject]@{
                elapsed_ms = [math]::Round(([DateTimeOffset]::UtcNow - $started).TotalMilliseconds, 3)
                stage = Get-QuickSdfStage -Path $stageOutput
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
    $baseline = @($samples | Where-Object { $_.stage -eq 'startup_baseline' })
    $steady = @($samples | Where-Object { $_.stage -eq 'residency_steady' })
    $baselinePrivate = if ($baseline.Count) { [int64](($baseline | Measure-Object private_bytes -Minimum).Minimum) } else { 0 }
    $steadyPrivate = if ($steady.Count) { [int64](($steady | Measure-Object private_bytes -Maximum).Maximum) } else { 0 }
    $exportSamples = @($samples | Where-Object { $_.stage -in @('export_snapshot_seconds', 'export_compute_seconds', 'export_file_seconds') })
    $exportPrivate = if ($exportSamples.Count) { [int64](($exportSamples | Measure-Object private_bytes -Maximum).Maximum) } else { 0 }
    $summary = [ordered]@{
        resolution = [int]$case.Resolution
        keys = [int]$case.Keys
        lane_mode = [string]$case.LaneMode
        baseline_private_bytes = $baselinePrivate
        steady_private_bytes = $steadyPrivate
        quick_sdf_steady_private_delta_bytes = [math]::Max(0, $steadyPrivate - $baselinePrivate)
        export_peak_private_delta_from_steady_bytes = [math]::Max(0, $exportPrivate - $steadyPrivate)
        peak_private_bytes = [int64](($samples | Measure-Object private_bytes -Maximum).Maximum)
        peak_working_set_bytes = [int64](($samples | Measure-Object working_set_bytes -Maximum).Maximum)
    }
    $summary | ConvertTo-Json | Set-Content -LiteralPath $summaryOutput -Encoding UTF8
    Write-Host "Measured $stem -> $probeOutput"
}
