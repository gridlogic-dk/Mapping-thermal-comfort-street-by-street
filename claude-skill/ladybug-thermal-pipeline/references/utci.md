# Outdoor Solar MRT + UTCI Comfort → `mrt`, `utci`

This is the trickiest analysis in the pipeline because MRT has the most
inputs and UTCI requires every one of them to be the correct shape and at
the correct timestep. When someone says "the UTCI isn't working", 90% of the
time it's a tree-shape or timestep mismatch on the MRT inputs.

## What goes into what

```
EPW ──▶ LB Import EPW ──┬─▶ dry_bulb_temperature  ──┐
                        ├─▶ rel_humidity           ──┤  (filter via
                        ├─▶ wind_speed              ─┤   LB Apply
                        ├─▶ dir_norm_rad             ─┤   Analysis Period
                        ├─▶ diff_horiz_rad           ─┤   before MRT/UTCI)
                        └─▶ horiz_infrared          ──┘

LB Direct Sun Hours ──▶ fract_body_exp  (per point per hour, 0..1)
LB View Analysis ─────▶ sky_exposure    (per point, 0..1, broadcast hourly)

         ┌─────────────────────────────┐
         │     LB Outdoor Solar MRT    │
         │  location, surf_temp,       │
         │  dir_norm_rad, diff_horiz,  │──▶ mrt (per point per hour, °C)
         │  horiz_infrared,            │
         │  fract_body_exp, sky_exp,   │
         │  ground_ref, body_par       │
         └─────────────────────────────┘
                          │
                          ▼
         ┌─────────────────────────────┐
         │     LB UTCI Comfort         │
         │  air_temp, rel_humidity,    │──▶ utci (per point per hour, °C)
         │  wind_speed, mrt            │
         └─────────────────────────────┘
```

The critical realisation: **MRT and UTCI are computed per (point, hour)**.
For a 12-hour design day at hourly timestep, that's a data tree shaped
`{2875 points} × {13 hours}` (or 12, depending on how the period includes
endpoints). Every input must conform to this shape, either natively
per-point-per-hour (like `fract_body_exp`) or broadcast (the EPW streams are
per-hour scalars; the MRT component broadcasts them across points).

## Period & timestep — pin these first

- `start_month=M`, `start_day=21`, `start_hour=6`, `end_hour=18`,
  **`timestep=1`** (hourly).
- The `LB Apply Analysis Period` filter on every EPW stream **must** use the
  *same* period block as the MRT/UTCI flow. Not "a period with the same
  values" — the same block. Wire one period to all consumers.

If the period block is at `timestep=6` (left over from the shadow analysis),
EPW data comes through at 6-min intervals and MRT will silently produce
nonsense or refuse to compute. **Check the timestep first** when UTCI is
broken.

## Inputs in detail

### `_location`
From `LB Import EPW`'s `location` output. Constant for the whole run.

### `_surface_temp`
The temperature of the ground surface(s) the body radiates to. For a
hot-climate analysis, two common approximations:

- **Lazy/fast**: feed the EPW's `dry_bulb_temperature` (filtered through the
  period). Treats the ground as being at air temperature.
- **More accurate**: run a surface-temperature analysis (e.g. an Energy+
  envelope sim) and feed its hourly trace. Out of scope for most street-level
  studies.

The lazy version is what the Dubai pipeline uses today. It underestimates
peak MRT in summer (asphalt can reach 60 °C while air is 42 °C), so flag this
to the user as a known limitation.

### `_dir_norm_rad`, `_diff_horiz_rad`, `_horiz_infrared`
All from `LB Import EPW`, all filtered through the period.

### `_fract_body_exp`
The big one. This is the fraction of the body that has direct line-of-sight
to the sun at each hour. It's *per point per hour* and comes from a
**Sun Shadow analysis run at hourly timestep**. You can use the same
`LB Direct Sun Hours` component, but with `timestep=1` so it emits one value
per hour rather than summing.

This is the input most likely to be the wrong shape. If it's
`{2875}` (one number per point, e.g. the daily fraction), MRT will throw a
data-tree warning. It must be `{2875} × {N_hours}`.

### `_sky_exposure`
Per-point sky view fraction (0..1), broadcast across all hours. Wire the SVF
analysis result directly. The MRT component handles the broadcast internally.

### `_ground_ref`
Albedo of the ground. Default 0.25 is fine for mixed urban. Use 0.40 for
sandy/desert, 0.15 for dense vegetation.

### `_solar_body_par`
Body posture parameter. Default `LB Solar Body Parameter` is fine for a
standing-person analysis.

## CSV the IronPython writer should produce

UTCI is the headline metric for downstream comfort categorisation. For most
pipelines, write per-month UTCI files with a *daily aggregate* (mean during
6 AM–6 PM, or max-during-window — both are useful, pipeline default is mean):

```
x, y, z, utci
12345.67, 89012.34, 1.5, 38.5
...
```

One file per month: `UTCI_result 1.csv` … `UTCI_result 12.csv` under
`02_Process/UTCI_Analysis/`.

If you also want hourly UTCI (for time-of-day plots), write a *separate*
wide CSV per month with one column per hour. Don't shoehorn it into the
pipeline schema.

## Bridge behaviour

- Read the daily-aggregate column (`utci` or `mean_utci`).
- No unit conversion (already °C).
- Merge into the per-month file: adds `utci` column to
  `grasshopper_results_month_NN.csv` that already holds shadow + radiation +
  svf.
- Optional: also extract `mrt` from a parallel `MRT_result <N>.csv` and add
  it as a column too.

## "It's not working" — UTCI-specific (the common failure modes)

- **MRT component is orange with "data tree shape mismatch"** → `fract_body_exp`
  is the wrong shape. Re-run the sun analysis at `timestep=1` and confirm
  the output tree is `{2875} × {13}` (or whatever your hour count is).
- **MRT component is red with "Input parameter '_location' failed"** → you
  wired the *EPW file path* to `_location` instead of the EPW component's
  `location` output. Add an `LB Import EPW` and use its `location` port.
- **UTCI values are all the same** across all points → MRT didn't vary per
  point, which means `fract_body_exp` or `sky_exposure` was broadcast as a
  scalar instead of varying per point. Inspect the MRT input trees.
- **UTCI throws "out of valid range" warnings** → input air temperature is
  outside −50..+50 °C, or wind speed is > 17 m/s. Check the EPW filter is
  actually filtering (could be emitting the full year).
- **UTCI runs but writes nothing** → CSV writer's `_run` toggle is False,
  or its output path's parent folder doesn't exist. Pre-create the folder.
- **`_run` is True but the component is grey** → the upstream period block
  is at `timestep != 1`. Fix the timestep, the rest will flow.

## Config snippet

```yaml
bridge_utci:
  input_dir: "02_Process/UTCI_Analysis"
  input_filename_template: "UTCI_result {month}.csv"
  input_value_column: "utci"

bridge_mrt:        # optional, if you also write MRT
  input_dir: "02_Process/UTCI_Analysis"
  input_filename_template: "MRT_result {month}.csv"
  input_value_column: "mrt"
```
