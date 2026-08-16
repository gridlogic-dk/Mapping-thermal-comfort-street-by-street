# Pipeline integration (config.yaml, Stage 4, Stage 5)

Adding a new metric is three small edits in three different files. This file
walks through where each edit goes and why.

## The 5-stage shape

| Stage | Script | Reads | Writes |
|---|---|---|---|
| 1 | `stage1_extract_metadata.py` | `Metadata/<area>/*.json` | `master_metadata.csv` |
| 2 | `stage2_analyze_segmentation.py` | `Segmentation/*.png` | `segmentation_metrics.csv` |
| 3 | `stage3_prepare_grasshopper.py` | `master_metadata.csv` | `points_for_grasshopper.csv` + `.geojson` |
| — | (manual) Grasshopper runs + bridges | raw GH CSVs | `Grasshopper_Output/grasshopper_results_month_NN.csv` |
| 4 | `stage4_merge_grasshopper_results.py` | metadata + segmentation + 12 monthly GH CSVs | `Merged_Data/merged_thermal_data.csv` (34,500 rows, long format) |
| 5 | `stage5_build_final_geojson.py` | the merged CSV | `dubai_thermal_complete.geojson` (one feature per point, monthly array + annual summary) |

Stage 4 is the only point where multiple metrics meet. Stage 5 is purely a
pivot+enrich step — it does not know about any specific metric, it just
walks columns.

## Three edits for a new metric

### 1. `config.yaml` — add the bridge config

```yaml
bridge_<name>:
  input_dir: "02_Process/<name>_Analysis"
  input_filename_template: "<name>_result {month}.csv"
  input_value_column: "<gh_column_name>"
  # any other knobs the bridge template supports
```

### 2. `config.yaml` — register the metric with Stage 4

```yaml
grasshopper:
  expected_columns:
    - point_id
    - month
    - shadow_hours
    - radiation_kwh
    - svf
    - utci
    - mrt
    - <new_metric>     # add here
```

Stage 4 reads this list to decide which columns it cares about. Missing
columns produce a warning, not a fatal error — the pipeline keeps running
with the metric as `null`.

### 3. `stage5_build_final_geojson.py` — add to the monthly object

Find the block that constructs `monthly.append({...})` and add a line:

```python
monthly.append({
    "month":         month_num,
    "month_name":    calendar.month_name[month_num] if month_num else None,
    "shadow_hours":  _col_or_none(row, "shadow_hours"),
    "radiation_kwh": _col_or_none(row, "radiation_kwh"),
    "svf":           _col_or_none(row, "svf"),
    "utci":          utci_val,
    "utci_category": category,
    "recommendation": action,
    "mrt":           _col_or_none(row, "mrt"),
    "<new_metric>":  _col_or_none(row, "<new_metric>"),   # add
})
```

`_col_or_none` already handles the "column doesn't exist" case, so Stage 5
won't crash if you forget to run the bridge first — it'll just emit `null`.

## Run order after wiring a new metric

```
python bridge_<name>_to_pipeline.py     # produces or augments the per-month GH CSVs
python stage4_merge_grasshopper_results.py
python stage5_build_final_geojson.py
```

You don't need to re-run stages 1, 2, 3 — they don't depend on the new metric.

## Stage 4 "missing column" warning — what it means

Stage 4 logs lines like:

```
Optional columns present: utci=False radiation_kwh=True svf=True mrt=False
```

This is informational, not an error. It tells you which metrics will end up
as `null` in the final GeoJSON. If you ran the bridge but Stage 4 still says
`<metric>=False`, three likely causes in priority order:

1. The bridge wrote a *different* column name than Stage 4 expects (typo, or
   the bridge cloned an older template). Open one of the per-month CSVs and
   confirm the header.
2. The bridge wrote to a different `output_dir` than Stage 4 reads from.
   They both resolve via `cfg["grasshopper"]["output_dir"]` — make sure both
   stages see the same config.
3. The bridge only updated *some* months. Stage 4 joins on (point_id, month);
   missing months produce nulls.

## Stage 5 "annual summary" — be aware of dependencies

Stage 5 computes a per-point annual summary:
`avg_utci`, `max_utci`, `min_utci`, `comfortable_months`, `hot_months`,
`planning_priority`. If `utci` is absent across all months, the summary
gracefully degrades (sets the UTCI fields to `null` and `planning_priority`
to `"Unknown - UTCI not computed yet"`).

When you wire up a new "primary" metric (something the pipeline should
summarise annually), think about whether Stage 5's annual summary needs to
know about it. For now only UTCI and shadow get annual summaries.

## Tests / sanity checks at each stage

- After Stage 3: open the GeoJSON in QGIS — the points should cluster on
  the study area.
- After each bridge: open one per-month CSV — should have `point_id, month,
  <metrics>`, 2,875 rows, plausible value range.
- After Stage 4: `wc -l Merged_Data/merged_thermal_data.csv` should be
  `12 × points + 1` (header). For 2,875 points that's 34,501.
- After Stage 5: pick a feature with a known position (e.g. a point next to
  an open plaza vs. one in a deep canyon) and confirm `svf` differs in the
  expected direction.
