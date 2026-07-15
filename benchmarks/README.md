# Quick SDF performance probes

`run_performance_benchmarks.ps1` starts every probe in a clean Blender 5.1
process.  The controller samples Windows Working Set and Private Bytes every
50 ms while the Blender-side probe records operation timings.

The release target can be measured with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_performance_benchmarks.ps1
```

Use `-FullMatrix` to cover 512/1024/2048/4096, 8/16/32 keys, and linked versus
independent lanes.  The full matrix is intentionally opt-in because the 4096
and 32-key cases are stress tests and can consume several GiB on an older
build. Results are written below `build/performance/` and are not committed.

The probe is deliberately headless and measures data-path costs rather than
mouse-event latency. Interactive Studio smoke tests remain the authority for
canvas correctness and perceived input latency.
