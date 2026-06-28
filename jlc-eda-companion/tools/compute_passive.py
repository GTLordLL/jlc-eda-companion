#!/usr/bin/env python3
"""阻容值计算器 — 欧姆定律、分压、滤波、晶振负载电容、555定时器计算。

Usage (CLI):
  python compute_passive.py ohms-law --voltage 5 --current 0.02
  python compute_passive.py voltage-divider --vin 5 --r1 10000 --r2 10000
  python compute_passive.py feedback-divider --vref 0.8 --vout 5 --r2 10000
  python compute_passive.py rc-filter --r 10000 --c 0.0000001
  python compute_passive.py crystal-load --cl 20e-12
  python compute_passive.py 555-astable --r1 10000 --r2 10000 --c 10e-9

Usage (import):
  from compute_passive import calc_ohms_law, nearest_standard
  result = calc_ohms_law(voltage=5, current=0.02)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Optional, Literal

# ── Standard Value Tables ─────────────────────────────────────────────

E24_VALUES: list[float] = [
    1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7,
    3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1,
]

E96_VALUES: list[float] = [
    1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24,
    1.27, 1.30, 1.33, 1.37, 1.40, 1.43, 1.47, 1.50, 1.54, 1.58,
    1.62, 1.65, 1.69, 1.74, 1.78, 1.82, 1.87, 1.91, 1.96, 2.00,
    2.05, 2.10, 2.15, 2.21, 2.26, 2.32, 2.37, 2.43, 2.49, 2.55,
    2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09, 3.16, 3.24,
    3.32, 3.40, 3.48, 3.57, 3.65, 3.74, 3.83, 3.92, 4.02, 4.12,
    4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23,
    5.36, 5.49, 5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65,
    6.81, 6.98, 7.15, 7.32, 7.50, 7.68, 7.87, 8.06, 8.25, 8.45,
    8.66, 8.87, 9.09, 9.31, 9.53, 9.76,
]

# Common capacitor E6 values (for capacitors which usually follow E6/E12)
E6_VALUES: list[float] = [1.0, 1.5, 2.2, 3.3, 4.7, 6.8]
E12_VALUES: list[float] = [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2]

SERIES_MAP: dict[str, list[float]] = {
    "E6": E6_VALUES,
    "E12": E12_VALUES,
    "E24": E24_VALUES,
    "E96": E96_VALUES,
}


# ── Unit Formatting ───────────────────────────────────────────────────

def _format_resistance(value: float) -> str:
    """Format resistance with SI prefix. 10000 -> '10kΩ'"""
    if value >= 1_000_000:
        return f"{value / 1_000_000:g}MΩ"
    elif value >= 1_000:
        return f"{value / 1_000:g}kΩ"
    else:
        return f"{value:g}Ω"


def _format_capacitance(value: float) -> str:
    """Format capacitance in farads to human-readable. 1e-7 -> '100nF'"""
    if value >= 1e-3:
        return f"{value * 1e3:g}mF"
    elif value >= 1e-6:
        return f"{value * 1e6:g}µF"
    elif value >= 1e-9:
        return f"{value * 1e9:g}nF"
    elif value >= 1e-12:
        return f"{value * 1e12:g}pF"
    else:
        return f"{value * 1e15:g}fF"


def _format_frequency(value: float) -> str:
    """Format frequency with SI prefix. 1000 -> '1kHz'"""
    if value >= 1_000_000:
        return f"{value / 1_000_000:g}MHz"
    elif value >= 1_000:
        return f"{value / 1_000:g}kHz"
    else:
        return f"{value:g}Hz"


def _format_voltage(value: float) -> str:
    """Format voltage. 5 -> '5V'"""
    return f"{value:g}V"


def _format_current(value: float) -> str:
    """Format current. 0.02 -> '20mA'"""
    if value >= 1:
        return f"{value:g}A"
    elif value >= 1e-3:
        return f"{value * 1e3:g}mA"
    elif value >= 1e-6:
        return f"{value * 1e6:g}µA"
    else:
        return f"{value * 1e9:g}nA"


# ── Standard Value Lookup ─────────────────────────────────────────────

def _expand_series(base_values: list[float]) -> list[float]:
    """Expand base E-series values with decade multipliers into full range.

    Covers 1pΩ-equivalent to 10MΩ-equivalent (works for both resistors and
    capacitors by interpreting the base as the mantissa).
    """
    result: list[float] = []
    for decade in [1e-12, 1e-11, 1e-10, 1e-9, 1e-8, 1e-7, 1e-6, 1e-5, 1e-4,
                    1e-3, 1e-2, 0.1, 1.0, 10.0, 100.0, 1_000.0, 10_000.0,
                    100_000.0, 1_000_000.0, 10_000_000.0]:
        for base in base_values:
            result.append(base * decade)
    return result


def nearest_standard(
    value: float,
    series: Literal["E6", "E12", "E24", "E96"] = "E24",
) -> dict:
    """Find the nearest standard E-series value.

    Args:
        value: The ideal value (e.g., 10470 for a 10.47kΩ resistor).
        series: E-series to search within.

    Returns:
        dict with keys: value, series, nearest, error_pct, alternatives (list of
        up to 2 next-closest values with their errors).
    """
    if value <= 0:
        raise ValueError(f"value must be positive, got {value}")

    base_values = SERIES_MAP[series]
    full_series = _expand_series(base_values)

    # Find nearest
    nearest_val = min(full_series, key=lambda v: abs(v - value))
    error_pct = (nearest_val / value - 1) * 100

    # Find alternatives (next closest)
    sorted_by_diff = sorted(full_series, key=lambda v: abs(v - value))
    alternatives: list[dict] = []
    for v in sorted_by_diff[1:4]:  # next 3 closest
        if v != nearest_val:
            alt_error = (v / value - 1) * 100
            alternatives.append({"value": v, "error_pct": round(alt_error, 2)})

    return {
        "value": value,
        "series": series,
        "nearest": nearest_val,
        "error_pct": round(error_pct, 2),
        "alternatives": alternatives[:2],
    }


# ── Core Calculation Functions ────────────────────────────────────────

def calc_ohms_law(
    *,
    voltage: Optional[float] = None,
    current: Optional[float] = None,
    resistance: Optional[float] = None,
) -> dict:
    """Solve V = I * R for the missing variable.

    Exactly two of the three must be provided.
    """
    given = [v for v in (voltage, current, resistance) if v is not None]
    if len(given) != 2:
        raise ValueError(
            f"必须恰好提供两个参数，当前提供了 {len(given)} 个"
        )

    inputs: dict[str, float] = {}
    known_lines: list[str] = []

    if voltage is None:
        # R = V / I
        result_val = current * resistance  # wait, V = I*R
        # Actually solving for V: V = I * R
        assert current is not None and resistance is not None
        result_val = current * resistance
        inputs = {"current": current, "resistance": resistance}
        known_lines = [
            f"已知：I = {_format_current(current)}, R = {_format_resistance(resistance)}",
            f"计算：V = I × R = {_format_current(current)} × {_format_resistance(resistance)} = {_format_voltage(result_val)}",
        ]
        outputs = {"voltage": result_val}
    elif current is None:
        # I = V / R
        assert voltage is not None and resistance is not None
        result_val = voltage / resistance
        inputs = {"voltage": voltage, "resistance": resistance}
        known_lines = [
            f"已知：V = {_format_voltage(voltage)}, R = {_format_resistance(resistance)}",
            f"计算：I = V / R = {_format_voltage(voltage)} / {_format_resistance(resistance)} = {_format_current(result_val)}",
        ]
        outputs = {"current": result_val}
    else:
        # R = V / I
        assert voltage is not None and current is not None
        result_val = voltage / current
        inputs = {"voltage": voltage, "current": current}
        known_lines = [
            f"已知：V = {_format_voltage(voltage)}, I = {_format_current(current)}",
            f"计算：R = V / I = {_format_voltage(voltage)} / {_format_current(current)} = {_format_resistance(result_val)}",
        ]
        outputs = {"resistance": result_val}

    return {
        "mode": "ohms_law",
        "inputs": inputs,
        "outputs": outputs,
        "formula": "V = I × R",
        "steps": known_lines,
    }


def calc_voltage_divider(
    *,
    vin: float,
    r1: Optional[float] = None,
    r2: Optional[float] = None,
    vout: Optional[float] = None,
) -> dict:
    """Voltage divider: Vout = Vin * R2 / (R1 + R2).

    Three of four variables must be provided; the fourth is computed.
    """
    given = [v for v in (r1, r2, vout) if v is not None]
    if len(given) != 2:
        raise ValueError(
            f"必须恰好提供 r1, r2, vout 中的两个参数，当前提供了 {len(given)} 个"
        )

    inputs = {"vin": vin}
    steps: list[str] = []
    nearest: dict = {}

    if vout is None:
        assert r1 is not None and r2 is not None
        vout = vin * r2 / (r1 + r2)
        inputs.update({"r1": r1, "r2": r2})
        steps = [
            f"已知：Vin = {_format_voltage(vin)}, R1 = {_format_resistance(r1)}, R2 = {_format_resistance(r2)}",
            f"计算：Vout = Vin × R2 / (R1 + R2)",
            f"     = {_format_voltage(vin)} × {_format_resistance(r2)} / ({_format_resistance(r1)} + {_format_resistance(r2)})",
            f"     = {_format_voltage(vin)} × {_format_resistance(r2)} / {_format_resistance(r1 + r2)}",
            f"     = {_format_voltage(vout)}",
        ]
    elif r1 is None:
        assert r2 is not None and vout is not None
        r1 = r2 * (vin - vout) / vout
        inputs.update({"r2": r2, "vout": vout})
        steps = [
            f"已知：Vin = {_format_voltage(vin)}, Vout = {_format_voltage(vout)}, R2 = {_format_resistance(r2)}",
            f"计算：R1 = R2 × (Vin - Vout) / Vout",
            f"     = {_format_resistance(r2)} × ({_format_voltage(vin)} - {_format_voltage(vout)}) / {_format_voltage(vout)}",
            f"     = {_format_resistance(r1)}",
        ]
        nearest["r1"] = nearest_standard(r1)
    else:
        assert r1 is not None and vout is not None
        r2 = r1 * vout / (vin - vout)
        inputs.update({"r1": r1, "vout": vout})
        steps = [
            f"已知：Vin = {_format_voltage(vin)}, Vout = {_format_voltage(vout)}, R1 = {_format_resistance(r1)}",
            f"计算：R2 = R1 × Vout / (Vin - Vout)",
            f"     = {_format_resistance(r1)} × {_format_voltage(vout)} / ({_format_voltage(vin)} - {_format_voltage(vout)})",
            f"     = {_format_resistance(r2)}",
        ]
        nearest["r2"] = nearest_standard(r2)

    outputs = {"r1": r1, "r2": r2, "vout": vout}
    result: dict = {
        "mode": "voltage_divider",
        "inputs": inputs,
        "outputs": outputs,
        "formula": "Vout = Vin × R2 / (R1 + R2)",
        "steps": steps,
    }
    if nearest:
        result["nearest_standard"] = nearest
    return result


def calc_feedback_divider(
    *,
    vref: float,
    vout: Optional[float] = None,
    r1: Optional[float] = None,
    r2: Optional[float] = None,
) -> dict:
    """Feedback divider for voltage regulators: Vout = Vref * (1 + R1/R2).

    Typical Vref values:
      MP1584: 0.8V, TPS5430: 1.221V, LM2596: 1.23V, AMS1117-ADJ: 1.25V

    Two of {vout, r1, r2} must be provided (plus vref, which is always required).
    """
    given = [v for v in (r1, r2, vout) if v is not None]
    if len(given) != 2:
        raise ValueError(
            f"必须恰好提供 vout, r1, r2 中的两个参数，当前提供了 {len(given)} 个"
        )

    inputs = {"vref": vref}
    steps: list[str] = []
    nearest: dict = {}

    if vout is None:
        assert r1 is not None and r2 is not None
        vout = vref * (1 + r1 / r2)
        inputs.update({"r1": r1, "r2": r2})
        steps = [
            f"已知：Vref = {_format_voltage(vref)}, R1 = {_format_resistance(r1)}, R2 = {_format_resistance(r2)}",
            f"计算：Vout = Vref × (1 + R1/R2)",
            f"     = {_format_voltage(vref)} × (1 + {_format_resistance(r1)} / {_format_resistance(r2)})",
            f"     = {_format_voltage(vref)} × (1 + {r1/r2:.4f})",
            f"     = {_format_voltage(vout)}",
        ]
    elif r1 is None:
        assert r2 is not None and vout is not None
        r1 = r2 * (vout / vref - 1)
        inputs.update({"r2": r2, "vout": vout})
        steps = [
            f"已知：Vref = {_format_voltage(vref)}, Vout = {_format_voltage(vout)}, R2 = {_format_resistance(r2)}",
            f"计算：R1 = R2 × (Vout/Vref - 1)",
            f"     = {_format_resistance(r2)} × ({_format_voltage(vout)} / {_format_voltage(vref)} - 1)",
            f"     = {_format_resistance(r2)} × ({vout/vref:.4f} - 1)",
            f"     = {_format_resistance(r1)}",
        ]
        nearest["r1"] = nearest_standard(r1)
    else:
        assert r1 is not None and vout is not None
        r2 = r1 / (vout / vref - 1)
        inputs.update({"r1": r1, "vout": vout})
        steps = [
            f"已知：Vref = {_format_voltage(vref)}, Vout = {_format_voltage(vout)}, R1 = {_format_resistance(r1)}",
            f"计算：R2 = R1 / (Vout/Vref - 1)",
            f"     = {_format_resistance(r1)} / ({_format_voltage(vout)} / {_format_voltage(vref)} - 1)",
            f"     = {_format_resistance(r1)} / ({vout/vref:.4f} - 1)",
            f"     = {_format_resistance(r2)}",
        ]
        nearest["r2"] = nearest_standard(r2)

    # Verify
    vout_check = vref * (1 + r1 / r2)

    outputs = {"r1": r1, "r2": r2, "vout": vout_check}
    result: dict = {
        "mode": "feedback_divider",
        "inputs": inputs,
        "outputs": outputs,
        "formula": "Vout = Vref × (1 + R1/R2)",
        "steps": steps,
        "verification": {
            "expected_vout": vout,
            "actual_vout": vout_check,
            "error_pct": round((vout_check / vout - 1) * 100, 3) if vout else 0,
        },
    }
    if nearest:
        result["nearest_standard"] = nearest
    return result


def calc_rc_cutoff(
    *,
    r: Optional[float] = None,
    c: Optional[float] = None,
    fc: Optional[float] = None,
) -> dict:
    """RC low-pass filter cutoff frequency: fc = 1 / (2 * pi * R * C).

    Two of three variables must be provided.
    """
    given = [v for v in (r, c, fc) if v is not None]
    if len(given) != 2:
        raise ValueError(
            f"必须恰好提供两个参数，当前提供了 {len(given)} 个"
        )

    steps: list[str] = []
    nearest: dict = {}

    if fc is None:
        assert r is not None and c is not None
        fc = 1 / (2 * math.pi * r * c)
        inputs = {"r": r, "c": c}
        steps = [
            f"已知：R = {_format_resistance(r)}, C = {_format_capacitance(c)}",
            f"计算：fc = 1 / (2π × R × C)",
            f"     = 1 / (2π × {_format_resistance(r)} × {_format_capacitance(c)})",
            f"     = 1 / (2π × {r:.6g} × {c:.6g})",
            f"     = {_format_frequency(fc)}",
        ]
    elif r is None:
        assert c is not None and fc is not None
        r = 1 / (2 * math.pi * fc * c)
        inputs = {"c": c, "fc": fc}
        steps = [
            f"已知：fc = {_format_frequency(fc)}, C = {_format_capacitance(c)}",
            f"计算：R = 1 / (2π × fc × C)",
            f"     = 1 / (2π × {_format_frequency(fc)} × {_format_capacitance(c)})",
            f"     = {_format_resistance(r)}",
        ]
        nearest["r"] = nearest_standard(r)
    else:
        assert r is not None and fc is not None
        c = 1 / (2 * math.pi * fc * r)
        inputs = {"r": r, "fc": fc}
        steps = [
            f"已知：fc = {_format_frequency(fc)}, R = {_format_resistance(r)}",
            f"计算：C = 1 / (2π × fc × R)",
            f"     = 1 / (2π × {_format_frequency(fc)} × {_format_resistance(r)})",
            f"     = {_format_capacitance(c)}",
        ]
        # For capacitors, use E6 or E12
        nearest["c"] = nearest_standard(c, "E6")

    # Recompute for verification
    fc_check = 1 / (2 * math.pi * r * c)

    result: dict = {
        "mode": "rc_filter",
        "inputs": inputs,
        "outputs": {"r": r, "c": c, "fc": fc_check},
        "formula": "fc = 1 / (2π × R × C)",
        "steps": steps,
    }
    if nearest:
        result["nearest_standard"] = nearest
    return result


def calc_crystal_load(
    *,
    c1: Optional[float] = None,
    c2: Optional[float] = None,
    cl: Optional[float] = None,
    cstray: float = 3e-12,
) -> dict:
    """Crystal load capacitance: CL = (C1 * C2) / (C1 + C2) + Cstray.

    Typically C1 == C2, so: C1 = C2 = 2 * (CL - Cstray).

    Provide either:
    - c1, c2 (and optional cstray) to compute CL
    - cl (and optional cstray) to compute C1=C2
    """
    steps: list[str] = []
    nearest: dict = {}

    if cl is None:
        assert c1 is not None and c2 is not None
        cl = (c1 * c2) / (c1 + c2) + cstray
        inputs = {"c1": c1, "c2": c2, "cstray": cstray}
        steps = [
            f"已知：C1 = {_format_capacitance(c1)}, C2 = {_format_capacitance(c2)}, Cstray = {_format_capacitance(cstray)}",
            f"计算：CL = (C1 × C2) / (C1 + C2) + Cstray",
            f"     = ({_format_capacitance(c1)} × {_format_capacitance(c2)}) / ({_format_capacitance(c1)} + {_format_capacitance(c2)}) + {_format_capacitance(cstray)}",
            f"     = {_format_capacitance(cl)}",
        ]
    else:
        # Assume symmetric: C1 = C2 = 2 * (CL - Cstray)
        c_load_target = max(cl - cstray, 0)
        c1 = c2 = 2 * c_load_target
        inputs = {"cl": cl, "cstray": cstray}
        steps = [
            f"已知：CL = {_format_capacitance(cl)}, Cstray = {_format_capacitance(cstray)}（典型值）",
            f"计算（对称，C1=C2）：",
            f"  C_load = CL - Cstray = {_format_capacitance(cl)} - {_format_capacitance(cstray)} = {_format_capacitance(c_load_target)}",
            f"  C1 = C2 = 2 × C_load = 2 × {_format_capacitance(c_load_target)} = {_format_capacitance(c1 or 0)}",
        ]
        nearest["c1"] = nearest_standard(c1 or 0, "E6")
        nearest["c2"] = nearest_standard(c2 or 0, "E6")

    result: dict = {
        "mode": "crystal_load",
        "inputs": inputs,
        "outputs": {
            "c1": c1,
            "c2": c2,
            "cl": cl,
            "cstray": cstray,
        },
        "formula": "CL = (C1 × C2) / (C1 + C2) + Cstray",
        "steps": steps,
    }
    if nearest:
        result["nearest_standard"] = nearest
    return result


def calc_timer_555_astable(
    *,
    r1: float,
    r2: float,
    c: float,
) -> dict:
    """555 timer astable multivibrator.

    f = 1.44 / ((R1 + 2*R2) * C)
    Duty cycle = (R1 + R2) / (R1 + 2*R2)  [always > 50%]
    Th = 0.693 * (R1 + R2) * C
    Tl = 0.693 * R2 * C
    """
    th = 0.693 * (r1 + r2) * c
    tl = 0.693 * r2 * c
    period = th + tl
    frequency = 1 / period if period > 0 else float("inf")
    duty_cycle = th / period * 100 if period > 0 else 0

    steps = [
        f"已知：R1 = {_format_resistance(r1)}, R2 = {_format_resistance(r2)}, C = {_format_capacitance(c)}",
        f"计算：",
        f"  Th = 0.693 × (R1 + R2) × C = 0.693 × {_format_resistance(r1 + r2)} × {_format_capacitance(c)} = {th * 1e6:.2f}µs",
        f"  Tl = 0.693 × R2 × C = 0.693 × {_format_resistance(r2)} × {_format_capacitance(c)} = {tl * 1e6:.2f}µs",
        f"  T = Th + Tl = {(th + tl) * 1e6:.2f}µs",
        f"  f = 1 / T = {_format_frequency(frequency)}",
        f"  Duty Cycle = Th / T × 100% = {duty_cycle:.1f}%",
    ]

    return {
        "mode": "555_astable",
        "inputs": {"r1": r1, "r2": r2, "c": c},
        "outputs": {
            "frequency": frequency,
            "period": period,
            "duty_cycle_pct": round(duty_cycle, 1),
            "th": th,
            "tl": tl,
        },
        "formula": "f = 1.44 / ((R1 + 2×R2) × C)",
        "steps": steps,
    }


# ── Output Formatting ─────────────────────────────────────────────────

def _format_table(result: dict) -> str:
    """Format a calculation result as a Markdown table for user display."""
    mode = result["mode"]
    mode_names = {
        "ohms_law": "欧姆定律计算",
        "voltage_divider": "电阻分压计算",
        "feedback_divider": "反馈电阻分压计算",
        "rc_filter": "RC 低通滤波器截止频率",
        "crystal_load": "晶振负载电容计算",
        "555_astable": "555 定时器 (多谐振荡器)",
    }

    lines = [f"## {mode_names.get(mode, mode)}", "", "### 参数", ""]

    # Inputs
    lines.append("| 参数 | 值 |")
    lines.append("|------|-----|")
    for key, val in result["inputs"].items():
        formatted = _format_value(key, val)
        lines.append(f"| {_param_label(key)} | {formatted} |")

    # Outputs
    lines.append("")
    for key, val in result["outputs"].items():
        formatted = _format_value(key, val)
        # Bold the primary output
        key_label = _param_label(key)
        lines.append(f"- **{key_label}**：{formatted}")

    # Formula
    lines.append("")
    lines.append("### 计算公式")
    lines.append(f"```\n{result['formula']}\n```")

    # Steps
    lines.append("")
    lines.append("### 计算步骤")
    lines.append("")
    for step in result["steps"]:
        lines.append(f"  {step}")

    # Verification (if present)
    if "verification" in result:
        lines.append("")
        lines.append("### 验证")
        v = result["verification"]
        err = v.get("error_pct", 0)
        status = "✅ 通过" if abs(err) < 1 else "⚠️ 偏差较大"
        lines.append(f"目标 Vout = {_format_voltage(v.get('expected_vout', 0))}  "
                     f"实际 Vout = {_format_voltage(v.get('actual_vout', 0))}  "
                     f"误差 = {err}%  {status}")

    # Nearest standard values (if present)
    if "nearest_standard" in result:
        lines.append("")
        lines.append("### 最接近的标准值")
        lines.append("")
        lines.append("| 参数 | 理想值 | 最近标准值 | 误差 | 备选值 |")
        lines.append("|------|--------|-----------|------|--------|")
        for key, ns in result["nearest_standard"].items():
            ideal = ns["value"]
            nearest_v = ns["nearest"]
            err_pct = ns["error_pct"]
            series = ns["series"]
            alts = ", ".join(
                f"{_format_component(key, a['value'])} ({a['error_pct']:+.1f}%)"
                for a in ns.get("alternatives", [])[:2]
            )
            lines.append(
                f"| {_param_label(key)} | {_format_component(key, ideal)} "
                f"| {_format_component(key, nearest_v)} ({series}) "
                f"| {err_pct:+.1f}% "
                f"| {alts} |"
            )

    return "\n".join(lines)


def _param_label(key: str) -> str:
    """Convert parameter key to Chinese label."""
    labels = {
        "voltage": "电压 (V)",
        "current": "电流 (I)",
        "resistance": "电阻 (R)",
        "vin": "输入电压 (Vin)",
        "vout": "输出电压 (Vout)",
        "vref": "参考电压 (Vref)",
        "r": "电阻 (R)",
        "r1": "电阻 R1",
        "r2": "电阻 R2",
        "c": "电容 (C)",
        "c1": "电容 C1",
        "c2": "电容 C2",
        "cstray": "杂散电容 (Cstray)",
        "cl": "负载电容 (CL)",
        "fc": "截止频率 (fc)",
        "frequency": "频率 (f)",
        "period": "周期 (T)",
        "duty_cycle_pct": "占空比",
        "th": "高电平时间 (Th)",
        "tl": "低电平时间 (Tl)",
    }
    return labels.get(key, key)


def _format_value(key: str, val: float) -> str:
    """Format a value based on its parameter type."""
    if key in ("voltage", "vin", "vout", "vref"):
        return _format_voltage(val)
    elif key in ("current",):
        return _format_current(val)
    elif key in ("resistance", "r", "r1", "r2"):
        return _format_resistance(val)
    elif key in ("c", "c1", "c2", "cl", "cstray"):
        return _format_capacitance(val)
    elif key in ("fc", "frequency"):
        return _format_frequency(val)
    elif key in ("period", "th", "tl"):
        if val >= 1:
            return f"{val:g}s"
        elif val >= 1e-3:
            return f"{val * 1e3:g}ms"
        elif val >= 1e-6:
            return f"{val * 1e6:.2f}µs"
        else:
            return f"{val * 1e9:.2f}ns"
    elif key == "duty_cycle_pct":
        return f"{val}%"
    return f"{val}"


def _format_component(key: str, value: float) -> str:
    """Format a component value based on its type (resistor/capacitor)."""
    if key in ("r", "r1", "r2", "resistance"):
        return _format_resistance(value)
    elif key in ("c", "c1", "c2", "cstray", "cl", "capacitance"):
        return _format_capacitance(value)
    return str(value)


# ── Value Parsing (for CLI) ───────────────────────────────────────────

def parse_resistance(s: str) -> float:
    """Parse human-readable resistance string to ohms.

    Examples: '10k' -> 10000, '4.7K' -> 4700, '1M' -> 1000000, '220R' -> 220, '100' -> 100
    """
    s = s.strip().upper().replace("Ω", "").replace("OHM", "")
    multipliers = {"M": 1_000_000, "K": 1_000, "R": 1}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            return float(s[:-1]) * mult
    return float(s)


def parse_capacitance(s: str) -> float:
    """Parse human-readable capacitance string to farads.

    Examples: '100nF' -> 1e-7, '10uF' -> 1e-5, '1pF' -> 1e-12, '100n' -> 1e-7
    """
    s = s.strip().upper().replace("F", "").replace("Μ", "U")  # μ → U
    multipliers = {"M": 1e-3, "U": 1e-6, "N": 1e-9, "P": 1e-12}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            return float(s[:-1]) * mult
    # No suffix? Treat as farads directly
    return float(s)


def parse_frequency(s: str) -> float:
    """Parse human-readable frequency to Hz.

    Examples: '1kHz' -> 1000, '10MHz' -> 1e7, '100' -> 100
    """
    s = s.strip().upper().replace("HZ", "")
    multipliers = {"M": 1_000_000, "K": 1_000}
    for suffix, mult in multipliers.items():
        if s.endswith(suffix):
            return float(s[:-1]) * mult
    return float(s)


# ── CLI ────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="阻容值计算器 — 欧姆定律、分压、滤波、晶振负载电容、555定时器计算",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python compute_passive.py ohms-law --voltage 5 --current 0.02
  python compute_passive.py voltage-divider --vin 5 --r1 10k --r2 10k
  python compute_passive.py feedback-divider --vref 0.8 --vout 5 --r2 10k
  python compute_passive.py rc-filter --r 10k --c 100nF
  python compute_passive.py crystal-load --cl 20pF
  python compute_passive.py 555-astable --r1 10k --r2 10k --c 10nF
        """,
    )
    # Common arguments for all subcommands
    def _add_common(p):
        p.add_argument("--format", choices=["json", "table"], default="table",
                       help="输出格式 (default: table)")
        p.add_argument("--series", choices=["E6", "E12", "E24", "E96"], default="E24",
                       help="标准值系列 (default: E24)")

    sub = parser.add_subparsers(dest="mode", required=True)

    # ohms-law
    p = sub.add_parser("ohms-law", help="欧姆定律计算 (V=IR)")
    _add_common(p)
    p.add_argument("--voltage", "-v", type=float, help="电压 (V)")
    p.add_argument("--current", "-i", type=float, help="电流 (A)")
    p.add_argument("--resistance", "-r", type=float, help="电阻 (Ω)")

    # voltage-divider
    p = sub.add_parser("voltage-divider", help="电阻分压 (Vout = Vin × R2/(R1+R2))")
    _add_common(p)
    p.add_argument("--vin", required=True, type=float, help="输入电压 (V)")
    p.add_argument("--r1", type=parse_resistance, help="电阻 R1 (Ω, 支持 10k)")
    p.add_argument("--r2", type=parse_resistance, help="电阻 R2 (Ω, 支持 10k)")
    p.add_argument("--vout", type=float, help="输出电压 (V)")

    # feedback-divider
    p = sub.add_parser("feedback-divider", help="反馈电阻分压 (Vout = Vref × (1+R1/R2))")
    _add_common(p)
    p.add_argument("--vref", required=True, type=float, help="参考电压 (V), 如 0.8 (MP1584), 1.25 (AMS1117)")
    p.add_argument("--vout", type=float, help="目标输出电压 (V)")
    p.add_argument("--r1", type=parse_resistance, help="电阻 R1 (Ω)")
    p.add_argument("--r2", type=parse_resistance, help="电阻 R2 (Ω)")

    # rc-filter
    p = sub.add_parser("rc-filter", help="RC 低通滤波器截止频率 (fc = 1/(2πRC))")
    _add_common(p)
    p.add_argument("--r", type=parse_resistance, help="电阻 (Ω)")
    p.add_argument("--c", type=parse_capacitance, help="电容 (F, 支持 100nF)")
    p.add_argument("--fc", type=parse_frequency, help="截止频率 (Hz, 支持 1kHz)")

    # crystal-load
    p = sub.add_parser("crystal-load", help="晶振负载电容 (CL = (C1×C2)/(C1+C2) + Cstray)")
    _add_common(p)
    p.add_argument("--c1", type=parse_capacitance, help="电容 C1 (F)")
    p.add_argument("--c2", type=parse_capacitance, help="电容 C2 (F)")
    p.add_argument("--cl", type=parse_capacitance, help="目标负载电容 (F), 通常从晶振数据手册获取")
    p.add_argument("--cstray", type=parse_capacitance, default=3e-12, help="杂散电容 (F), 默认 3pF")

    # 555-astable
    p = sub.add_parser("555-astable", help="555 定时器多谐振荡器 (f = 1.44/((R1+2R2)C))")
    _add_common(p)
    p.add_argument("--r1", required=True, type=parse_resistance, help="电阻 R1 (Ω)")
    p.add_argument("--r2", required=True, type=parse_resistance, help="电阻 R2 (Ω)")
    p.add_argument("--c", required=True, type=parse_capacitance, help="电容 (F)")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        result = _dispatch(args)
    except ValueError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(_format_table(result))


def _dispatch(args) -> dict:
    mode = args.mode
    series: Literal["E6", "E12", "E24", "E96"] = args.series

    if mode == "ohms-law":
        return calc_ohms_law(
            voltage=args.voltage,
            current=args.current,
            resistance=args.resistance,
        )

    elif mode == "voltage-divider":
        return calc_voltage_divider(
            vin=args.vin,
            r1=args.r1,
            r2=args.r2,
            vout=args.vout,
        )

    elif mode == "feedback-divider":
        return calc_feedback_divider(
            vref=args.vref,
            vout=args.vout,
            r1=args.r1,
            r2=args.r2,
        )

    elif mode == "rc-filter":
        return calc_rc_cutoff(
            r=args.r,
            c=args.c,
            fc=args.fc,
        )

    elif mode == "crystal-load":
        return calc_crystal_load(
            c1=args.c1,
            c2=args.c2,
            cl=args.cl,
            cstray=args.cstray,
        )

    elif mode == "555-astable":
        return calc_timer_555_astable(
            r1=args.r1,
            r2=args.r2,
            c=args.c,
        )

    else:
        raise ValueError(f"Unknown mode: {mode}")


if __name__ == "__main__":
    main()
