# Incident Radiation → `radiation_kwh`

Maps a Ladybug **LB Incident Radiation** analysis onto the pipeline's
`radiation_kwh` metric (total solar radiation incident on the test point
across the 6 AM – 6 PM design window, in kWh/m²).

## Grasshopper wiring

```
LB Analysis Period ──▶ LB Cumulative Sky Matrix ───────▶ _sky_mtx ──┐
EPW ────────────────▶                                                │
                                                                     ▼
points (Flatten) ───────────────────────────────────────▶ LB Incident
context meshes (Flatten) ───────────────────────────────▶  Radiation ──▶ results (kWh/m²)
```

- **`_period`**: same monthly-21st 6 AM–6 PM pattern, but `timestep=1`
  (hourly). Incident Radiation integrates the EPW's hourly DNI/DHI, so a
  finer timestep would just resample the same data.
- **`_sky_mtx`**: built once from EPW + period via `LB Cumulative Sky Matrix`.
  Re-bake it whenever the month slider changes.
- **`_geometry`** and **`context_`**: same flattened point list + building
  meshes as the shadow analysis. Reuse — don't rebuild.

## CSV the IronPython writer should produce

```
x, y, z, radiation_kwh
12345.67, 89012.34, 1.5, 5.42
...
```

If you cloned the shadow writer without updating the header, the column will
still be called `shadow_fraction`. The bridge handles both names — but
emit `radiation_kwh` if you can, it's less confusing.

One file per month: `Radiation_result 1.csv` … `Radiation_result 12.csv`,
under `02_Process/Radiation_Analysis/`.

## Bridge behaviour

- Read the value column, preferring `radiation_kwh` but falling back to
  `shadow_fraction` for legacy writers.
- No unit conversion — the LB component already outputs kWh/m² per point.
- Merge into the existing `grasshopper_results_month_NN.csv` (which already
  has `shadow_hours`) by adding a `radiation_kwh` column on the matching
  row index. Don't overwrite the file; *augment* it.
- Emit `point_id, month, shadow_hours, radiation_kwh`.

## "It's not working" — radiation-specific

- **All values are very small (< 0.1 kWh/m²)** → wrong period unit. The
  Cumulative Sky Matrix is reading a single hour, not a 12-hour window. Check
  the period block's `_period` input.
- **All values are very large (> 50 kWh/m²)** → wrong period. Probably the
  full year is leaking through. Same fix.
- **Component is red with "geometry has zero area"** → you wired a point list
  to a component that expects mesh faces. Use `LB Incident Radiation` (which
  accepts points) rather than the mesh-only variant.
- **Sky matrix re-bakes on every recompute and it's slow** → set the Cumulative
  Sky Matrix output to `Bake` once and disconnect it from upstream so it only
  recomputes when you intentionally re-run.

## Config snippet

```yaml
bridge_radiation:
  input_dir: "02_Process/Radiation_Analysis"
  input_filename_template: "Radiation_result {month}.csv"
```

(No `value_unit` needed — radiation is already in kWh/m².)
