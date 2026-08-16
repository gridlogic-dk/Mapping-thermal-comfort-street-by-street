---
name: ladybug-thermal-pipeline
description: Set up, debug, and integrate Ladybug Tools analyses in Grasshopper (Direct Sun Hours, Incident Radiation, Sky View Factor, Outdoor Solar MRT, UTCI Comfort) that feed a Python street-level thermal-comfort pipeline. Use whenever the user mentions Ladybug, Grasshopper, UTCI, MRT, SVF, sky view factor, shadow hours, incident radiation, thermal comfort points, or has a project structured around 12 monthly design days that exports CSVs which a Python pipeline merges into a per-point GeoJSON. Also use when the user asks why a Grasshopper Ladybug analysis "isn't working", complains about empty/garbage CSV output, mismatched data-tree shapes, off-by-one row counts, NUL bytes in CSV, or needs to write a Python "bridge" script that converts raw GH output to a pipeline-friendly format. This skill assumes a config-driven pipeline with shadow_hours, radiation_kwh, svf, utci, and mrt as the target metrics, but the patterns generalize to any Ladybug-to-Python integration.
---

# Ladybug → Python Thermal Comfort Pipeline

This skill captures the end-to-end pattern for wiring Ladybug Tools analyses in
Grasshopper to a Python merging pipeline that produces a per-point monthly
thermal-comfort GeoJSON. It exists because the same three problems show up
every time someone builds one of these:

1. The Grasshopper data-tree shapes don't match between the geometry input and
   the analysis component, so the component either dies silently or produces
   nonsense.
2. The CSV that Grasshopper writes has trailing NUL bytes, BOM markers, or an
   off-by-one row, and naive `pd.read_csv` chokes on it.
3. The units don't match: sun hours land on disk as "sunny ticks" (timesteps),
   SVF as a percentage instead of a fraction, radiation per timestep instead of
   per day.

Use this skill to walk a user through new analyses, to debug an analysis that
"isn't working", or to write the Python bridge that converts raw GH output to
the pipeline format.

## When to reach for the references

The body below covers the workflow and patterns. For component-specific wiring,
expected CSV columns, and the "it's not working" checklist for each analysis,
open the relevant reference file:

| You're working on | Read |
|---|---|
| Direct Sun Hours / shadow_hours | `references/shadow.md` |
| Incident Radiation / radiation_kwh | `references/radiation.md` |
| Sky View Factor / svf | `references/svf.md` |
| Outdoor Solar MRT + UTCI | `references/utci.md` |
| Writing the Python bridge | `references/bridge_pattern.md` |
| Pipeline config & Stage-4 merge | `references/pipeline_integration.md` |

Each reference is self-contained — read only the one(s) you need.

## The pipeline shape (one paragraph)

A point cloud (typically lat/lng/elevation pulled from street-view metadata) is
exported to Grasshopper as a CSV plus GeoJSON. Grasshopper, against context
geometry (building meshes), runs one Ladybug analysis per design day — by
convention the 21st of each month, 6 AM to 6 PM. Each analysis writes a CSV
per month. A Python "bridge" script per analysis converts the raw GH CSV to
pipeline format (`point_id, month, <metric>`). A Stage-4 merge joins all
metrics on `(point_id, month)` into a long-format CSV; Stage 5 pivots that into
a per-point GeoJSON with monthly arrays plus an annual summary. The pipeline is
config-driven (`config.yaml`) so the same code runs on any city by editing
paths and coordinates.

## The non-negotiable contracts

Everything in the pipeline assumes these contracts hold. If a new analysis
violates one, write a bridge to fix it on the Python side — don't change the
pipeline schema.

- **Point IDs are stable across stages.** They're derived from the image
  filename and normalized once (spaces → underscores by default). Every CSV
  written by every bridge must use the same `point_id` set in the same order
  as `points_for_grasshopper.csv`.
- **One row per (point_id, month) in pipeline CSVs.** No wide tables, no
  per-hour rows. If Grasshopper produced hourly values, the bridge aggregates
  to a daily total or daily mean *before* writing.
- **Column names match what Stage 4 expects.** Currently: `shadow_hours`,
  `radiation_kwh`, `svf`, `utci`, `mrt`. If you add a new metric, add it to
  `grasshopper.expected_columns` in `config.yaml` and to Stage 5's monthly
  template, otherwise it'll silently disappear from the final GeoJSON.
- **SVF and other geometric metrics broadcast across all 12 months.** They're
  not time-dependent. The bridge reads one source file and writes 12
  identical-per-point pipeline files.
- **Coordinates live in metadata, not in pipeline metric CSVs.** Stage 4 joins
  metric CSVs against `master_metadata.csv` for `lat`/`lng`/`elevation`/
  `pano_id`. Bridges only emit the metric.

## Component wiring — the universal pattern

Every Ladybug environmental analysis (sun hours, radiation, SVF, MRT) takes
the same three inputs in some form:

```
                         ┌─────────────────────────┐
   _geometry  ──────────▶│                         │
   context_   ──────────▶│   LB Analysis Component │──▶ results (tree)
   _vectors / _sky_dome ▶│                         │
                         └─────────────────────────┘
```

- **`_geometry`**: the test points (or test mesh) you want results *at*. For
  street-level pipelines this is always the 2,875 panorama points lifted to
  eye level (1.5 m). Wire as `Construct Point` from the CSV's `lng/lat`
  (after projection) and a constant Z.
- **`context_`**: every piece of geometry that can cast shadow, block sky, or
  reflect light. Typically a flattened `Mesh` list of all building meshes in a
  ~500 m radius. Underprovisioning context is the #1 reason SVF / shadow
  results look "too good".
- **`_vectors` (sun analyses) or `_sky_dome` (SVF)**: the radiation field.
  Built from the EPW + an Analysis Period for sun analyses, or from the
  Tregenza sky for SVF.

The trees-must-match rule: if `_geometry` is `{0;0}` with N items, results
come out as `{0;0}` with N items. If you graft or flatten on the way in, the
output shape changes and the CSV writer downstream will silently truncate or
duplicate. Always `Flatten` `_geometry` and `context_` before they reach the
analysis component, and `Flatten` the result before writing.

## Period & timestep conventions

The pipeline uses 12 design days, the 21st of each month, 6 AM to 6 PM. Build
the period inside Grasshopper from sliders (start_month, start_day=21,
start_hour=6, end_hour=18) so you can sweep months without rewiring.

Timestep depends on the analysis:

| Analysis | Timestep | Why |
|---|---:|---|
| Direct Sun Hours | 6 | Coarser is fine; you're counting sunny ticks per day. |
| Incident Radiation | 1 (hourly) | Energy integration needs the EPW's hourly values. |
| SVF | n/a | Sky-dome based, time-independent. |
| Outdoor Solar MRT | 1 (hourly) | MRT is computed per hour; UTCI consumes it per hour. |
| UTCI Comfort | 1 (hourly) | Same. |

If a UTCI run "isn't working" and the MRT component is throwing tree-shape
warnings, the first thing to check is whether the analysis period timestep
matches between the EPW filter (LB Apply Analysis Period) and the sun-vector
generator (LB SunPath). They both must be 1 for UTCI.

## Diagnosing "it's not working"

When the user reports a Ladybug component is failing, work down this list
before touching the wiring:

1. **Is the component orange or red?** Hover for the tooltip; the error text
   is usually exact. Don't guess.
2. **Are the input data-tree shapes compatible?** Right-click each input,
   "Reparameterize" off, and use `Param Viewer` (set to draw tree) to inspect.
   Most "isn't working" failures are a tree-shape mismatch: 2,875 points × 12
   hours expected, but the EPW data is wired in as a flat 8,760 hours.
3. **Does the EPW filter precede the analysis component?** Raw EPW data is
   8,760 hours; the analysis wants only the design-day hours. `LB Apply
   Analysis Period` on every weather stream before it reaches MRT/UTCI.
4. **Is `_run` set to `True`?** A Boolean Toggle wired to `_run` that's still
   `False` is a common cause of "component looks fine but no output".
5. **Are the CSV writer paths writable?** If the path includes a folder that
   doesn't exist, GH writes nothing and reports success. Pre-create the output
   folder.

For analysis-specific failure modes (MRT requires `sky_exposure` but you
passed SVF in percent, etc.), see the per-analysis reference files.

## Bridge scripts — what every one of them does

The bridges all follow the same skeleton; bundled at `scripts/bridge_template.py`.
Copy it, change three things, ship it:

1. The config key (`bridge_shadow`, `bridge_radiation`, `bridge_svf`, …) and the
   input filename template (e.g. `"shadow_results {month}.csv"`).
2. The value column name to read from GH's CSV (`shadow_fraction`,
   `radiation_kwh`, `svf`, …) and the column name to emit in the pipeline CSV.
3. The unit conversion (sunny_ticks → hours; percent → fraction; raw hourly
   → daily total). If the source can be ambiguous (e.g. 0–1 vs 0–100), make it
   auto-detect from the max value and log the decision.

The template handles, once and for all:

- Trailing NUL bytes from GH's pre-allocated file writer (`.rstrip(b"\x00 \r\n\t")`).
- UTF-8 and UTF-16 BOM (decode to UTF-8 before parsing).
- Trailing blank rows (`dropna(how="all")`).
- An off-by-one row count (trim if `len(df) == len(point_ids) + 1`).
- Row-index alignment against `points_for_grasshopper.csv` so `point_id` is
  always correctly attached.

Don't write these defenses from scratch — they're easy to skip and hard to
debug. See `references/bridge_pattern.md` for the full rationale, and
`scripts/bridge_template.py` for the runnable starting point.

## Config-driven pipeline integration

Every new analysis needs three config additions, in order:

```yaml
# 1) Tell the bridge where to find the raw GH files and how to read them.
bridge_<name>:
  input_dir: "02_Process/<name>_Analysis"
  input_filename_template: "<name>_result {month}.csv"
  input_value_column: "<gh_column>"
  # optional knobs the template supports:
  value_unit: "auto"            # auto | hours | sunny_ticks | percent | fraction
  raw_is_sunny_ticks: true      # only for shadow-like metrics

# 2) Tell Stage 4 the metric exists.
grasshopper:
  expected_columns:
    - point_id
    - month
    - <new_metric>          # add it here

# 3) Tell Stage 5 to put it on the monthly object.
#    (Code change in stage5_build_final_geojson.py — add a line to the
#     `monthly.append({...})` block.)
```

Run order after wiring a new metric: bridge_<name>.py → stage4 → stage5.
Stage 4 is the join; if the bridge wrote files but Stage 4 still warns "missing
column", check that the bridge actually emitted that column name (not a stale
typo from a previous metric).

## The "I have raw GH CSVs, what now?" recipe

When the user has freshly-baked Grasshopper output and wants to get it into the
pipeline:

1. Open one of the raw CSVs in a text editor (not Excel — Excel hides the NUL
   bytes). Confirm the column you intend to read exists and the row count
   matches `points_for_grasshopper.csv`. Note the value range.
2. If the value range surprises you (e.g. 0–72 when you expected 0–12), that's
   the timestep multiplication. The bridge will fix it; just confirm what unit
   the file is in so `value_unit` in config is right.
3. Write or adapt a bridge from `scripts/bridge_template.py`. Add the config
   block. Run it: `python bridge_<name>_to_pipeline.py`.
4. Open one of the written `grasshopper_results_month_NN.csv` files; confirm
   `point_id, month, <metric>` and a plausible range.
5. Run `python stage4_merge_grasshopper_results.py`; confirm no "missing
   column" warning for your new metric.
6. Run `python stage5_build_final_geojson.py`; open the GeoJSON, pick a
   feature, scroll its `monthly_analysis[]` — the new metric should be there
   for all 12 months.

If the user reports Stage 5's log saying `metric=False`, the bridge ran but
either (a) the column name in the bridge output doesn't match what Stage 5
looks for, or (b) Stage 4 didn't merge it because the per-month files don't
have that column. Inspect the merged CSV before re-running Stage 5.

## What this skill deliberately does *not* cover

- Building the Grasshopper file from scratch with no template. This skill
  assumes a working LB SunPath / EPW import / point-loading scaffold exists.
- Validating EPW data quality. If `Dubai.epw` is wrong, every downstream
  result is wrong — but that's an EPW question, not a pipeline one.
- The Phase-2 dashboard (the localhost viz). That's a separate skill if
  needed.
- Generic Grasshopper / Rhino API help. For raw Rhino scripting use the
  `rhino` MCP tools directly; this skill is specifically about the Ladybug
  → Python pipeline pattern.
