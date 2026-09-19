#!/usr/bin/env python3
"""Measure directional cues in actual 48 kHz stereo float32 capture windows.

The source is the same repeating broadband signal for every capture. Record
after settling, through the real renderer and symmetric headphone EQ. Positive
ILD means the left ear is louder; positive ITD means the right ear is later.
No absolute stream start time or waveform phase is used as a pose assertion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


REQUIRED = (
    "position_fl", "position_fr", "position_fc", "position_rl", "position_rr",
    "pose_neutral", "pose_yaw_plus90", "pose_yaw_minus90", "pose_yaw_180",
    "pose_pitch_plus45", "pose_pitch_minus45", "pose_roll_plus45", "pose_roll_minus45",
    "pose_pitch_plus45_roll_plus90", "pose_pitch_minus45_roll_plus90",
    "pose_neutral_repeat", "pose_recentered", "pose_recentered_yaw_plus90",
)
BANDS = ((200, 400), (400, 800), (800, 1600), (1600, 3200),
         (3200, 6400), (6400, 10000), (10000, 16000))
# Engineering regression bounds, not universal anatomical constants. Keep a
# gap between equivalent and distinct responses; do not fit these to a failed
# renderer capture. Geometry supplies the equivalences independently of PCM.
MAXIMUM_EQUIVALENT_SPECTRAL_DB = 0.8
MINIMUM_DISTINCT_SPECTRAL_DB = 1.0


class SpatialMetricsError(RuntimeError):
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


def _spectrum(samples, rate):
    # The fixture repeats exactly every 100 ms. Any complete period has the
    # same DFT magnitude regardless of where recording began (DFT shift
    # theorem). A Hann taper breaks that property: 50% overlap samples only
    # two phases of this *same* noise period, not independent noise records.
    # This estimator is specific to the periodic fixture, not arbitrary PCM.
    if rate % 10:
        raise ValueError("sample rate must represent an integral 100 ms period")
    size = rate // 10
    periods = len(samples) // size
    if not periods:
        raise ValueError("capture is too short for a 100 ms analysis window")
    blocks = samples[:periods * size].reshape(periods, size, 2)
    blocks = blocks - blocks.mean(axis=1, keepdims=True)
    power = np.mean(np.abs(np.fft.rfft(blocks, axis=1)) ** 2, axis=0)
    frequencies = np.fft.rfftfreq(size, 1 / rate)
    band_power = np.array([power[(frequencies >= lo) & (frequencies < hi)].mean(axis=0)
                           for lo, hi in BANDS])
    # Remove common output gain while retaining interaural and spectral shape.
    scale = power[(frequencies >= 200) & (frequencies < 16000)].mean()
    return 10 * np.log10(np.maximum(band_power, 1e-30) / max(float(scale), 1e-30))


def _interaural_delay(samples, rate):
    # The shared 200–1800 Hz band reduces pinna coloration bias. Ordinary
    # normalized correlation preserves coherent signal energy; it does not
    # amplify weak/noisy bins as an unqualified phase-only estimate would.
    size = 1 << (2 * len(samples) - 1).bit_length()
    spectra = np.fft.rfft(samples - samples.mean(axis=0), n=size, axis=0)
    frequencies = np.fft.rfftfreq(size, 1 / rate)
    band = (frequencies >= 200) & (frequencies <= 1800)
    filtered = np.fft.irfft(spectra * band[:, None], n=size, axis=0)
    energy = np.sqrt(np.square(filtered[:, 0]).sum() * np.square(filtered[:, 1]).sum())
    if energy <= 1e-20:
        raise ValueError("capture has no coherent low-frequency stimulus energy")
    cross = np.fft.irfft(np.conj(spectra[:, 0]) * spectra[:, 1] * band, n=size)
    maximum = int(round(rate * 0.0015))
    lags = np.arange(-maximum, maximum + 1)
    scores = cross[lags % size] / energy
    best = int(np.argmax(scores))
    if best in (0, len(scores) - 1):
        raise ValueError("interaural delay lies outside the physical ±1.5 ms search interval")
    left, center, right = map(float, scores[best - 1:best + 2])
    curvature = left - 2 * center + right
    fraction = 0.5 * (left - right) / curvature if abs(curvature) > 1e-15 else 0.0
    delay = (float(lags[best]) + max(-0.5, min(0.5, fraction))) * 1e6 / rate
    return delay, center


def _distance(a, b):
    return float(np.sqrt(np.mean((np.asarray(a) - np.asarray(b)) ** 2)))


def measure_window(path, sample_rate=48000):
    path = Path(path)
    raw = path.read_bytes()
    if len(raw) % 8:
        raise ValueError("capture is not complete interleaved stereo float32 frames")
    samples = np.frombuffer(raw, dtype="<f4").reshape(-1, 2).astype(np.float64)
    if len(samples) < int(sample_rate * 0.8):
        raise ValueError("capture must contain at least 800 ms of settled audio")
    if not np.isfinite(samples).all():
        raise ValueError("capture contains NaN or infinity")
    peak = float(np.max(np.abs(samples)))
    # Analyze a central 800 ms interval, avoiding recorder edge transients.
    start = (len(samples) - int(sample_rate * 0.8)) // 2
    samples = samples[start:start + int(sample_rate * 0.8)]
    rms = np.sqrt(np.square(samples).mean(axis=0))
    if min(rms) <= 1e-7:
        raise ValueError("one or both headphone channels are silent")
    if peak >= 0.999:
        raise ValueError("capture reaches digital clipping; direction metrics are invalid")
    spectrum = _spectrum(samples, sample_rate)
    delay, coherence = _interaural_delay(samples, sample_rate)
    half = len(samples) // 2
    first, second = samples[:half], samples[half:]
    first_rms = np.sqrt(np.square(first).mean(axis=0))
    second_rms = np.sqrt(np.square(second).mean(axis=0))
    return {
        "path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
        "frames": len(raw) // 8, "analyzed_frames": len(samples),
        "peak": peak, "rms_left": float(rms[0]), "rms_right": float(rms[1]),
        "ild_db_left_minus_right": float(20 * np.log10(rms[0] / rms[1])),
        "itd_us_right_minus_left": delay, "low_band_correlation": coherence,
        "normalized_band_db": spectrum.tolist(),
        "within_window_spectral_drift_db": _distance(
            _spectrum(first, sample_rate), _spectrum(second, sample_rate)),
        "within_window_gain_drift_db": float(np.max(np.abs(20 * np.log10(first_rms / second_rms)))),
    }


def analyze_windows(windows, sample_rate=48000):
    """Return measurements and checks; attach partial evidence to any failure."""
    report = {"status": "failed", "sample_rate": sample_rate,
              "format": "stereo f32le", "windows": {}, "checks": [],
              "spectrum_bands_hz": [list(band) for band in BANDS],
              "spectral_estimator": {"window": "rectangular complete stimulus periods",
                                     "period_frames": sample_rate // 10,
                                     "stimulus_period_ms": 100},
              "spectral_regression_bounds_db": {
                  "maximum_equivalent": MAXIMUM_EQUIVALENT_SPECTRAL_DB,
                  "minimum_distinct": MINIMUM_DISTINCT_SPECTRAL_DB},
              "convention": {"positive_ild": "left ear louder",
                             "positive_itd": "right ear later (source on left)"},
              "scope": "synthetic broadband signal, real software audio path; no subjective or hardware localization claim"}

    def check(condition, name, **evidence):
        report["checks"].append({"name": name, "passed": bool(condition), **evidence})

    def fail_if_needed():
        failed = [value["name"] for value in report["checks"] if not value["passed"]]
        if failed:
            report["error"] = "Acoustic checks failed: " + ", ".join(failed)
            raise SpatialMetricsError(report["error"], report)

    check(sample_rate == 48000, "capture-rate", actual=sample_rate, expected=48000)
    check(all(name in windows for name in REQUIRED), "all-source-and-pose-captures-present",
          missing=[name for name in REQUIRED if name not in windows])
    fail_if_needed()
    for name in REQUIRED:
        try:
            measurement = measure_window(windows[name], sample_rate)
        except (OSError, ValueError) as exc:
            raise SpatialMetricsError(f"Invalid acoustic capture {name}: {exc}", report) from exc
        report["windows"][name] = measurement
        check(measurement["low_band_correlation"] >= 0.6,
              name + ": coherent-binaural-signal", minimum=0.6,
              actual=measurement["low_band_correlation"])
        check(measurement["within_window_spectral_drift_db"] <= 0.8
              and measurement["within_window_gain_drift_db"] <= 1.0,
              name + ": settled-window", maximum_spectral_db=0.8, maximum_gain_db=1.0,
              spectral_db=measurement["within_window_spectral_drift_db"],
              gain_db=measurement["within_window_gain_drift_db"])

    measured = report["windows"]

    def lateral(name, side, minimum_delay, minimum_ild):
        value = measured[name]
        ild, delay = value["ild_db_left_minus_right"], value["itd_us_right_minus_left"]
        sign = 1 if side == "left" else -1
        check(sign * ild >= minimum_ild and minimum_delay <= sign * delay <= 1100,
              name + ": expected-" + side + "-ear-leads-and-is-louder",
              ild_db=ild, itd_us=delay, minimum_ild_db=minimum_ild,
              minimum_abs_itd_us=minimum_delay, maximum_abs_itd_us=1100)

    for name, side in (("position_fl", "left"), ("position_fr", "right"),
                       ("position_rl", "left"), ("position_rr", "right")):
        lateral(name, side, 100, 1)
    # +Y head-to-world yaw turns the listener left, so fixed front sound moves
    # right. Qx(+45)*Qz(+90) puts fixed front at head-relative left 45 degrees.
    lateral("pose_yaw_plus90", "right", 350, 3)
    lateral("pose_yaw_minus90", "left", 350, 3)
    lateral("pose_recentered_yaw_plus90", "right", 350, 3)
    lateral("pose_pitch_plus45_roll_plus90", "left", 160, 1)
    lateral("pose_pitch_minus45_roll_plus90", "right", 160, 1)

    for name in ("position_fc", "pose_neutral", "pose_yaw_180",
                 "pose_pitch_plus45", "pose_pitch_minus45", "pose_roll_plus45",
                 "pose_roll_minus45", "pose_neutral_repeat", "pose_recentered"):
        value = measured[name]
        maximum_ild = 2.5
        check(abs(value["ild_db_left_minus_right"]) <= maximum_ild
              and abs(value["itd_us_right_minus_left"]) <= 120,
              name + ": median-plane-source", maximum_abs_ild_db=maximum_ild,
              maximum_abs_itd_us=120, ild_db=value["ild_db_left_minus_right"],
              itd_us=value["itd_us_right_minus_left"])

    def compare(first, second, equivalent):
        a, b = measured[first], measured[second]
        spectrum = _distance(a["normalized_band_db"], b["normalized_band_db"])
        ild = abs(a["ild_db_left_minus_right"] - b["ild_db_left_minus_right"])
        delay = abs(a["itd_us_right_minus_left"] - b["itd_us_right_minus_left"])
        if equivalent:
            check(spectrum <= MAXIMUM_EQUIVALENT_SPECTRAL_DB and ild <= 0.6 and delay <= 50,
                  first + ": matches-" + second, spectral_rms_db=spectrum,
                  ild_difference_db=ild, itd_difference_us=delay,
                  maximum_spectral_rms_db=MAXIMUM_EQUIVALENT_SPECTRAL_DB,
                  maximum_ild_difference_db=0.6,
                  maximum_itd_difference_us=50)
        else:
            check(spectrum >= MINIMUM_DISTINCT_SPECTRAL_DB,
                  first + ": distinct-directional-spectrum-from-" + second,
                  spectral_rms_db=spectrum,
                  minimum_spectral_rms_db=MINIMUM_DISTINCT_SPECTRAL_DB)

    # A front source lies on the roll axis: rotating around that axis cannot
    # move it. Roll still has to work on an off-axis source, as tested by the
    # pitch/roll compositions below. A stereo downmix of FC breaks this rule.
    for name in ("position_fc", "pose_roll_plus45", "pose_roll_minus45",
                 "pose_neutral_repeat", "pose_recentered"):
        compare(name, "pose_neutral", True)
    compare("pose_recentered_yaw_plus90", "pose_yaw_plus90", True)
    # Pinned Omniphony v0.5.2 virtual_bed.rs::fallback_virtual_bed_pose puts
    # C at (0,1,0), FL/FR at (+/-1,1,0), BL/BR at (+/-1,-1,0) in ADM axes.
    # Qz(-90) Qx(-/+45) in core axes transforms front to relative FL/FR at
    # -/+45 degrees and zero elevation. Equal relative source directions
    # must produce equal binaural cues, regardless of tracker/source route.
    compare("pose_pitch_plus45_roll_plus90", "position_fl", True)
    compare("pose_pitch_minus45_roll_plus90", "position_fr", True)
    for name in ("pose_yaw_180", "pose_pitch_plus45", "pose_pitch_minus45"):
        compare(name, "pose_neutral", False)
    compare("pose_pitch_plus45", "pose_pitch_minus45", False)
    # Rear channels must retain independent spatial responses. Requiring
    # equality here would certify an upstream stereo downmix as correct.
    compare("position_rl", "position_fl", False)
    compare("position_rr", "position_fr", False)
    for left, right in (("position_fl", "position_fr"), ("position_rl", "position_rr"),
                        ("pose_yaw_minus90", "pose_yaw_plus90"),
                        ("pose_pitch_plus45_roll_plus90", "pose_pitch_minus45_roll_plus90")):
        asymmetry = abs(measured[left]["itd_us_right_minus_left"]
                        + measured[right]["itd_us_right_minus_left"])
        check(asymmetry <= 200, left + ": mirror-delay-of-" + right,
              summed_itd_us=asymmetry, maximum_us=200)
    # Absolute ITD ordering between the yaw fixture and a diagonal bed
    # channel is renderer/HRTF dependent. The lateral() checks above already
    # require both directions to carry the expected sign and delay magnitude.
    fail_if_needed()
    report["status"] = "passed"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", required=True, type=Path, help="JSON object mapping capture names to f32le paths")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = analyze_windows(json.loads(args.windows.read_text()))
    except SpatialMetricsError as exc:
        report = exc.report
        report["error"] = str(exc)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "error": report.get("error")}, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
