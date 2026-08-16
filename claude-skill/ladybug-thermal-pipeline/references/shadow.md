# Direct Sun Hours → `shadow_hours`

Maps a Ladybug **LB Direct Sun Hours** analysis onto the pipeline's
`shadow_hours` metric (hours of shadow during the 6 AM – 6 PM design window,
so the value range is 0–12).

## Grasshopper wiring

```
LB Analysis Period ─┐                                ┌─▶ results (sun hours)
                    ├─▶ LB SunPath ─ vectors ──┐     │
EPW location ──────▶│                          ├──▶ LB Direct Sun Hours
                    └─▶ hoys (optional)        │     │
                                                │     │
points (Flatten) ──────────────────────────────┘     │
context meshes (Flatten) ─────────────────────────────┘
```

- **`_period`**: built from sliders: `start_month=M`, `start_day=21`,
  `start_hour=6`, `end_hour=18`. The "M" slider is what you sweep across
  months. `timestep=6` is fine — it just means 6 samples per hour, so the
  count per day is 0..72 ticks rather than 0..12 hours.
- **`_geometry`**: panorama points at 1.5 m. Make sure this is **flat**
  (`{0}`) before it enters the component, otherwise results come back as
  `{0;0;...;0}` and the CSV writer misaligns.
- **`context_`**: every building mesh in a ~500 m radius. Under-provisioned
  context is the most common cause of "shadow_hours looks too low" — the
  test points see "open sky" where they shouldn't.
- **`_run`**: Boolean Toggle = `True`. Easy to forget.

## CSV the IronPython writer should produce

```
x, y, z, shadow_fraction
12345.67, 89012.34, 1.5, 64.0
...
```

The header `shadow_fraction` is a holdover — it actually contains sunny ticks
(0..72) or sun hours (0..12) depending on the writer. The bridge auto-detects.

One file per month, named `shadow_results 1.csv` … `shadow_results 12.csv`,
saved under `02_Process/Shadow_Analysis/`.

## Bridge behaviour

- Read raw column (`shadow_fraction` regardless of actual unit).
- Auto-detect unit:
  - `max ≤ analysis_hours * 1.1` → unit is `hours`, use raw value directly.
  - `max ≤ analysis_hours * timestep * 1.1` → unit is `sunny_ticks`, divide
    by `timestep` to get sun hours.
  - Otherwise → unknown, raise (likely a multi-day run instead of single
    design day).
- Convert: `shadow_hours = analysis_hours - sun_hours` if
  `raw_is_sunny_ticks` is true; otherwise the raw value already *is* shadow
  hours.
- Clip to `[0, analysis_hours]` to mop up floating-point edge cases.
- Emit `point_id, month, shadow_hours` with `point_id` taken from
  `points_for_grasshopper.csv` in row order.

## "It's not working" — shadow-specific

- **All values are 0** → context geometry is empty *or* the points are
  embedded *inside* the building meshes. Lift Z slightly (e.g. to 1.5 m
  above mesh top) and re-bake the context.
- **All values equal `analysis_hours`** → no sun reaching the points. Check
  that `LB SunPath` is fed the actual EPW location, not a default; check the
  timezone is +4 for Dubai.
- **Row count off by 1** → trailing blank line in CSV, bridge handles it
  automatically. If the diff is ±2 or more, the GH writer is appending stale
  output from a previous run; clear the file and re-bake.
- **Values in the thousands** → wrong period (probably the full year, not
  one design day). Check the period sliders are wired to the actual analysis
  period input, not bypassed.

## Config snippet

```yaml
bridge_shadow:
  input_dir: "02_Process/Shadow_Analysis"
  input_filename_template: "shadow_results {month}.csv"
  input_value_column: "shadow_fraction"
  analysis_hours: 12
  timestep: 6
  value_unit: "auto"
  raw_is_sunny_ticks: true
```
