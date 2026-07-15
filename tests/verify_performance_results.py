from __future__ import annotations

import argparse
import json
from pathlib import Path


MIB = 1024 * 1024


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the Quick SDF 0.7 performance probe")
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    return parser.parse_args()


def _limit(value: float, maximum: float, label: str) -> None:
    if float(value) > float(maximum):
        raise AssertionError(f"{label}: {value:.6f}s exceeds {maximum:.6f}s")


def main() -> None:
    args = _arguments()
    probe = json.loads(args.probe.read_text(encoding="utf-8-sig"))
    summary = json.loads(args.summary.read_text(encoding="utf-8-sig"))
    timings = probe["timings"]
    resolution = int(probe["resolution"])
    keys = int(probe["keys"])
    lane_mode = str(probe["lane_mode"])
    if int(probe["native_abi"]) < 7:
        raise AssertionError("Performance acceptance requires Native ABI 7")
    if int(probe["resident_display_images"]) > 3 and resolution >= 2048:
        raise AssertionError("More than Active + adjacent Display images remain resident")

    _limit(timings["warm_key_switch_seconds"], 0.050, "warm key switch")
    _limit(timings["warm_seek_seconds"], 0.016, "warm seek")
    if resolution == 1024:
        _limit(timings["display_gray_roundtrip_seconds"], 0.150, "1024 gray stroke roundtrip")
    elif resolution == 2048:
        _limit(timings["cold_key_switch_seconds"], 0.120, "2048 cold key switch")
        _limit(timings["display_gray_roundtrip_seconds"], 0.300, "2048 gray stroke roundtrip")
        _limit(timings["export_file_seconds"], 5.0, "2048 export worker")
    elif resolution == 4096:
        _limit(timings["cold_key_switch_seconds"], 0.250, "4096 cold key switch")
        _limit(timings["export_file_seconds"], 15.0, "4096 export worker")

    accounted = int(probe["accounted_memory"]["total_bytes"])
    if resolution == 2048 and keys == 16 and lane_mode == "MIRROR":
        if accounted > 400 * MIB:
            raise AssertionError(
                f"Quick SDF live memory: {accounted / MIB:.1f} MiB exceeds 400 MiB"
            )
        export_peak = int(summary["export_peak_private_delta_from_steady_bytes"])
        if export_peak > 512 * MIB:
            raise AssertionError(
                f"Export process peak: {export_peak / MIB:.1f} MiB exceeds 512 MiB"
            )
    if resolution == 4096:
        export_peak = int(summary["export_peak_private_delta_from_steady_bytes"])
        if export_peak > int(1.5 * 1024 * MIB):
            raise AssertionError(
                f"4K export process peak: {export_peak / MIB:.1f} MiB exceeds 1536 MiB"
            )

    print(
        "[Quick SDF performance] PASS: "
        f"{resolution}px/{keys}/{lane_mode}, "
        f"live={accounted / MIB:.1f} MiB, export={timings['export_file_seconds']:.3f}s"
    )


if __name__ == "__main__":
    main()
