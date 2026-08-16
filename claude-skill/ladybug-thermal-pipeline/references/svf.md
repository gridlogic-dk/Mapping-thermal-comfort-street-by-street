# Sky View Factor → `svf`

Maps a Ladybug **LB View Analysis** (with `_view_type=4 SkyView`) onto the
pipeline's `svf` metric (fraction of sky hemisphere visible from each point,
0..1).

## SkyView vs SkyExposure — which one?

`LB View Analysis` has two sky-related options:

- **`_view_type = 3` (SkyExposure)**: equal-weight ray-cast across the sky
  hemisphere. Each direction counts the same.
- **`_view_type = 4` (SkyView)**: cosine-weighted (more weight to the
  zenith). This is the canonical "Sky View Factor" used in urban climate
  research, including UTCI / MRT calculations.

**Use `SkyView` (4) for thermal comfort work.** The MRT component's
`sky_exposure` input expects 0..1 in the SkyView sense.

## Grasshopper wiring

```
LB Tregenza Sky / Reinhart Sky ────▶ _study_mesh (sky dome) ──┐
                                                               ▼
points (Flatten) ──────────────────▶ _geometry ───▶ LB View
context meshes (Flatten) ──────────▶ context_ ────▶ Analysis ──▶ results (0..1)
                                                       │
                                            _view_type = 4  (SkyView)
```

- **`_view_type` = 4**: critical. Defaulting to 3 (SkyExposure) gives
  systematically higher numbers and breaks downstream MRT.
- **No time dependence**: this analysis runs once. No period slider, no sun
  vectors. The output covers all 12 months.

## CSV the IronPython writer should produce

A single file (not per-month), e.g.
`02_Process/Sky Value factor_Analysis/svf_results.csv`:

```
x, y, z, svf
12345.67, 89012.34, 1.5, 0.62
...
```

Column name variants the bridge tolerates: `svf`, `view_percent`, `results`,
`sky_view`. The bridge auto-picks the first numeric column matching one of
these.

## Bridge behaviour (broadcast pattern)

- Read the single source file.
- Auto-normalize: if `max(value) > 1.5`, divide by 100 (percent → fraction).
- Clip to `[0, 1]`.
- For each of months 1..12: open the existing
  `grasshopper_results_month_NN.csv`, add an `svf` column with the same
  per-point values, write it back.

This "broadcast" pattern is unique to geometric metrics (SVF, possibly
`green_view_index`). Time-dependent metrics get per-month files instead.

## "It's not working" — SVF-specific

- **Mean SVF is suspiciously high (> 0.7) in a dense urban area** →
  context geometry is missing buildings on one side, or `_view_type` is set
  to 3 (SkyExposure) instead of 4 (SkyView). The dense-urban range for street
  level is roughly 0.3–0.55; > 0.7 reads more like a parking lot or open
  plaza.
- **All values are very close together (e.g. 0.85 ± 0.02)** → context
  geometry is too far away or the rays are escaping out the top of the
  context box. Make sure context extends well above any test point.
- **File not found by bridge** → folder name and filename are
  case-sensitive on some setups. The pipeline default is
  `02_Process/Sky Value factor_Analysis/svf_results.csv` (with spaces, plural
  `results`). Match the config path exactly to what's on disk.
- **Values are in percent (0..100)** → the bridge handles this; if you'd
  rather emit a fraction directly from GH, divide by 100 inside the IronPython
  writer.

## Config snippet

```yaml
bridge_svf:
  input_file: "02_Process/Sky Value factor_Analysis/svf_results.csv"
```
