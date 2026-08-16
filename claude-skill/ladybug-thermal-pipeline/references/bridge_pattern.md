# Bridge script pattern

Every analysis ends the same way: Grasshopper writes a CSV that's *almost*
right, and a small Python "bridge" converts it to the pipeline's exact
contract. This file explains the why behind each defensive step so you can
adapt the template intelligently rather than copy-paste-and-pray.

## Why bridges exist at all

The pipeline keeps a strict schema: `point_id, month, <metric>`, one row per
(point_id, month). Raw GH output doesn't meet this schema because:

- GH writes `x, y, z, value` — no `point_id`, no `month`.
- GH writers have a few well-known quirks (NUL bytes, BOM, blank rows).
- GH may emit a different unit than the pipeline wants (sunny ticks vs hours,
  percent vs fraction).
- Some metrics are time-independent (SVF) but need to be broadcast across
  all 12 monthly files.

If you push these conversions into the pipeline's merge stage, the merge stage
becomes a swamp of metric-specific special cases. Pushing them into a
per-metric bridge keeps the merge stage trivially generic.

## The skeleton (mental model)

```python
def convert_all(cfg, only_months=None):
    logger = setup_logging(cfg, "bridge_<name>")
    bridge = cfg["bridge_<name>"]

    input_dir = resolve_path(cfg, bridge["input_dir"])
    points_csv = resolve_path(cfg, cfg["grasshopper"]["input_csv"])
    point_ids  = pd.read_csv(points_csv)["point_id"].astype(str).tolist()

    template = bridge["input_filename_template"]
    files = _find_per_month_files(input_dir, template)

    out_dir = resolve_path(cfg, cfg["grasshopper"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    for month, src in files.items():
        df = _read_robust_csv(src)
        df = _clean_blanks_and_trim(df, expected=len(point_ids))
        df = _to_pipeline_units(df, bridge, logger)
        out = pd.DataFrame({
            "point_id":  point_ids,
            "month":     month,
            "<metric>":  df["<col>"],
        })
        _merge_or_write(out, out_dir / f"grasshopper_results_month_{month:02d}.csv")
```

Each helper exists to handle one specific failure mode you'd otherwise have to
re-discover yourself.

## `_read_robust_csv` — handling GH writer quirks

GH's IronPython CSV writer doesn't truncate the file when it writes. If you
ran a previous analysis that produced 3,000 rows and then re-ran with 2,875,
the file still has the old 3,000-row length with NUL bytes after row 2,876.
`pd.read_csv` chokes on those.

Cure:

```python
raw = path.read_bytes()
cleaned = raw.rstrip(b"\x00 \r\n\t")
if cleaned.startswith(b"\xef\xbb\xbf"):
    cleaned = cleaned[3:]            # strip UTF-8 BOM
elif cleaned.startswith(b"\xff\xfe") or cleaned.startswith(b"\xfe\xff"):
    cleaned = cleaned.decode("utf-16").encode("utf-8")  # UTF-16 → UTF-8
return pd.read_csv(io.BytesIO(cleaned))
```

## `_clean_blanks_and_trim` — handling row-count drift

- **All-NaN rows**: drop them. They come from a stray newline at end of file.
- **Off-by-one**: if the row count is `len(point_ids) + 1` after dropping
  NaN rows, trim the last row. This is almost always a phantom row from the
  writer.
- **Off by anything else**: refuse to continue. A drift > 1 means the
  geometry input list to the GH analysis was a different length from the
  pipeline's `points_for_grasshopper.csv`, and silently re-aligning would
  scramble every point's results. Raise loudly.

## `_to_pipeline_units` — the auto-detect pattern

Where possible, *detect* the unit rather than asking the user to declare it.
The shadow bridge is a good example:

```python
hours_cap = analysis_hours * 1.1
ticks_cap = analysis_hours * timestep * 1.1
max_val   = float(raw.max())
if max_val <= hours_cap:
    unit = "hours"
elif max_val <= ticks_cap:
    unit = "sunny_ticks"
else:
    unit = "unknown"   # raise — almost certainly a multi-day run
```

For SVF:

```python
if raw.max() > 1.5:
    svf = raw / 100.0   # percent → fraction
else:
    svf = raw           # already 0..1
```

In both cases, **log the auto-detection**: `"unit auto-detected: sunny_ticks
(raw max=72.0)"`. When something goes sideways, the log makes it obvious why.

Allow the user to override via config (`value_unit: "hours"` etc.) — the
auto-detect is a sane default, not a mandate.

## `_merge_or_write` — augment vs overwrite

The first bridge to run for a month (typically `shadow`) writes a fresh file.
Subsequent bridges (`radiation`, `svf`, `utci`) need to **augment** that file
by adding a column, not overwrite it. Pattern:

```python
out_path = out_dir / f"grasshopper_results_month_{month:02d}.csv"
if out_path.exists():
    existing = pd.read_csv(out_path)
    existing["<new_metric>"] = new_values
    existing.to_csv(out_path, index=False)
else:
    # no shadow file yet, write fresh
    pd.DataFrame({...}).to_csv(out_path, index=False)
```

Important: align by *row index*, not by joining on `point_id`. Both sources
share the same row order from `points_for_grasshopper.csv`; joining on
`point_id` is slower and risks dropping rows if there are typos.

## Logging

Every bridge logs the same useful triplet per file:

```
Converting Radiation_result 6.csv (month=6)
  unit auto-detected: kWh (raw max=7.42)
  -> grasshopper_results_month_06.csv rows=2875 radiation_kwh min=0.12 max=7.42 mean=4.31
```

The `min/max/mean` summary catches "all values are zero" or "all values are
maxed out" without you having to open the CSV.

## When to deviate from the template

- **Hourly data needed downstream**: write a separate wide CSV
  (`utci_hourly_month_06.csv`) with one column per hour. Don't try to encode
  it into the pipeline's long-format schema.
- **Per-day rather than per-month design**: change the filename template to
  include day, and adjust Stage 4 accordingly. Doable but currently the
  pipeline assumes per-month.
- **Metric is a category, not a number** (e.g. UTCI thermal stress class):
  pipeline already does this in Stage 5 via `comfort_thresholds`. Emit the
  raw numeric in the bridge; let Stage 5 categorise.
