"""
Bridge template: convert raw Grasshopper CSVs -> pipeline per-month CSVs.
========================================================================

Copy this file to bridge_<name>_to_pipeline.py, edit the three TODOs near
the top, and the rest takes care of itself:

  1. METRIC_NAME             — the column name in the pipeline output
  2. CONFIG_KEY              — which `bridge_<key>` block in config.yaml
  3. extract_value(df, col)  — how to map the raw GH column to your metric

Defensive behaviour built in:
  - Strips trailing NUL bytes / UTF-8 / UTF-16 BOMs from GH writer output.
  - Drops fully blank trailing rows.
  - Trims a single off-by-one row (GH writer's stale-newline bug).
  - Refuses to silently realign if row drift is > 1.
  - Auto-detects unit (hours vs sunny_ticks, percent vs fraction) where
    applicable, and logs the decision.
  - Augments (does not overwrite) existing per-month CSV when a previous
    bridge has already populated `shadow_hours` etc.

Run:
    python bridge_<name>_to_pipeline.py                # all months found
    python bridge_<name>_to_pipeline.py --month 6      # one month only
"""
from __future__ import annotations

import argparse
import io
import re
from pathlib import Path

import pandas as pd

from utils import load_config, resolve_path, setup_logging


# ---------------------------------------------------------------------------
# TODO: customise these three things per metric.
# ---------------------------------------------------------------------------
METRIC_NAME = "your_metric"          # column name in the pipeline CSV
CONFIG_KEY  = "bridge_your_metric"   # block in config.yaml

def extract_value(df: pd.DataFrame, bridge_cfg: dict, logger) -> pd.Series:
    """
    Take the raw GH dataframe and return a Series of values in pipeline units.
    Tweak this per metric. Default below: read the configured column, no
    conversion. For unit detection, see the `_detect_*` helpers below.
    """
    col = bridge_cfg["input_value_column"]
    if col not in df.columns:
        raise KeyError(
            f"missing column '{col}'. Found: {list(df.columns)}"
        )
    return pd.to_numeric(df[col], errors="coerce")
# ---------------------------------------------------------------------------


def _read_robust_csv(path: Path) -> pd.DataFrame:
    """Strip trailing NUL/whitespace bytes + BOMs before parsing."""
    raw = path.read_bytes()
    cleaned = raw.rstrip(b"\x00 \r\n\t")
    if cleaned.startswith(b"\xef\xbb\xbf"):
        cleaned = cleaned[3:]
    elif cleaned.startswith(b"\xff\xfe") or cleaned.startswith(b"\xfe\xff"):
        cleaned = cleaned.decode("utf-16").encode("utf-8")
    return pd.read_csv(io.BytesIO(cleaned))


def _list_input_files(input_dir: Path, template: str) -> dict[int, Path]:
    """Resolve a 'foo_result {month}.csv' template to {month: path}."""
    pattern = re.escape(template).replace(re.escape("{month}"), r"(\d{1,2})")
    rx = re.compile("^" + pattern + "$", re.IGNORECASE)
    found: dict[int, Path] = {}
    for f in sorted(input_dir.iterdir()):
        if not f.is_file():
            continue
        m = rx.match(f.name)
        if m:
            found[int(m.group(1))] = f
    return found


def _clean_blanks_and_trim(df: pd.DataFrame, expected: int, logger) -> pd.DataFrame:
    """Drop all-NaN rows; trim a single phantom trailing row if needed."""
    before = len(df)
    df = df.dropna(how="all").reset_index(drop=True)
    if len(df) != before:
        logger.info("  dropped %d blank row(s)", before - len(df))
    if len(df) == expected + 1:
        logger.info("  trimming 1 extra row (file=%d, expected=%d)", len(df), expected)
        df = df.iloc[:expected].reset_index(drop=True)
    if len(df) != expected:
        raise ValueError(
            f"row count {len(df)} != expected {expected}. Refusing to "
            "silently realign — re-run the Grasshopper analysis against "
            "the same point list."
        )
    return df


# ---------------------------------------------------------------------------
# Helper unit detectors — call these from extract_value() if you need them.
# ---------------------------------------------------------------------------
def detect_hours_or_ticks(raw: pd.Series, analysis_hours: float,
                           timestep: int, logger) -> tuple[pd.Series, str]:
    """For sun-hour-like metrics. Returns (series_in_hours, detected_unit)."""
    max_val = float(raw.max())
    if max_val <= analysis_hours * 1.1:
        logger.info("  unit auto-detected: hours (raw max=%.2f)", max_val)
        return raw, "hours"
    if max_val <= analysis_hours * timestep * 1.1:
        logger.info("  unit auto-detected: sunny_ticks (raw max=%.2f)", max_val)
        return raw / timestep, "sunny_ticks"
    raise ValueError(
        f"raw max {max_val:.1f} exceeds both hour cap ({analysis_hours}) and "
        f"tick cap ({analysis_hours * timestep}). Likely a multi-day run."
    )


def detect_fraction_or_percent(raw: pd.Series, logger) -> pd.Series:
    """For 0..1-or-0..100 metrics like SVF. Returns 0..1 series."""
    max_val = float(raw.max())
    if max_val > 1.5:
        logger.info("  unit auto-detected: percent (raw max=%.2f), dividing by 100", max_val)
        return raw / 100.0
    logger.info("  unit auto-detected: fraction (raw max=%.4f)", max_val)
    return raw
# ---------------------------------------------------------------------------


def _augment_or_write(out_path: Path, point_ids: list[str], month: int,
                      values: pd.Series) -> pd.DataFrame:
    """If a per-month file exists, add the metric as a column; else create it."""
    if out_path.exists():
        existing = pd.read_csv(out_path)
        if len(existing) != len(point_ids):
            raise ValueError(
                f"{out_path.name} has {len(existing)} rows, expected "
                f"{len(point_ids)}. Aborting to avoid scrambling rows."
            )
        existing[METRIC_NAME] = values.round(4).values
        existing.to_csv(out_path, index=False)
        return existing
    fresh = pd.DataFrame({
        "point_id":   point_ids,
        "month":      month,
        METRIC_NAME:  values.round(4).values,
    })
    fresh.to_csv(out_path, index=False)
    return fresh


def convert_all(cfg, only_months=None):
    logger = setup_logging(cfg, f"bridge_{METRIC_NAME}_to_pipeline")
    bridge = cfg[CONFIG_KEY]

    input_dir = resolve_path(cfg, bridge["input_dir"])
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Bridge input dir not found: {input_dir}")

    points_csv = resolve_path(cfg, cfg["grasshopper"]["input_csv"])
    point_ids = pd.read_csv(points_csv)["point_id"].astype(str).tolist()
    logger.info("Loaded %d point_ids from %s", len(point_ids), points_csv.name)

    template = bridge["input_filename_template"]
    found = _list_input_files(input_dir, template)
    if not found:
        raise RuntimeError(f"No files match '{template}' in {input_dir}")
    logger.info("Discovered %d file(s) matching '%s'", len(found), template)

    months_to_run = sorted(found.keys()) if not only_months else sorted(set(only_months))
    out_dir = resolve_path(cfg, cfg["grasshopper"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for month in months_to_run:
        if month not in found:
            logger.warning("Month %d not found - skipping", month)
            continue
        src = found[month]
        logger.info("Converting %s (month=%d)", src.name, month)

        if src.stat().st_size == 0:
            logger.error("  -> %s is empty (0 bytes) - skipping", src.name)
            continue

        try:
            df = _read_robust_csv(src)
            df = _clean_blanks_and_trim(df, expected=len(point_ids), logger=logger)
            values = extract_value(df, bridge, logger)
        except Exception as exc:
            logger.error("  -> failed: %s", exc)
            continue

        out_name = cfg["grasshopper"]["output_naming_template"].format(month=month)
        out_path = out_dir / out_name
        result_df = _augment_or_write(out_path, point_ids, month, values)
        logger.info(
            "  -> %s rows=%d %s min=%.2f max=%.2f mean=%.2f",
            out_path.name, len(result_df), METRIC_NAME,
            result_df[METRIC_NAME].min(),
            result_df[METRIC_NAME].max(),
            result_df[METRIC_NAME].mean(),
        )
        written.append(out_path)

    logger.info("Bridge complete: %d file(s) written to %s", len(written), out_dir)
    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--month", type=int, action="append")
    args = parser.parse_args()
    cfg = load_config(args.config)
    convert_all(cfg, only_months=args.month)


if __name__ == "__main__":
    main()
