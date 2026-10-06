# MMDEC data cleaning log

**Run date:** 2026-10-06  
**Inputs:** six source files in the project root  
**Walkthrough:** [`notebooks/01_data_audit_and_cleaning.ipynb`](../notebooks/01_data_audit_and_cleaning.ipynb)  
**Reproducers:** [`scripts/clean_mmdec.py`](../scripts/clean_mmdec.py), [`scripts/screen_tracks.py`](../scripts/screen_tracks.py)  
**Outputs:** `data/processed/`; source files are unchanged. The notebook shows audit evidence before it invokes each dataset’s cleaner.

## Rules applied

- Preserve raw files and retain source values in every cleaned AIS copy. Add separate interpreted/validity fields rather than overwriting encoded values.
- Remove repeated AIS_POS events only after checking every field within each repeated `(Mmsi, Date)` group; all values, including `chunk_folder` and `id_chunk`, matched. This removed 40,553 rows.
- In AIS_SPEC, remove only exact full-row repeats. Do not deduplicate conflicting reports that share `(Mmsi, Date, MessageType)`; 35 such differing rows/groups remain available for temporal interpretation.
- Treat AIS unavailable/reserved codes as missing only in new semantic fields. Keep the original code columns. Preserve dimension maxima as censored codes and keep zero dimensions as coded defaults.
- Preserve environmental spatial-mask nulls and all grid rows. Do not impute. Remove `VMXL` from the analytical copy because it is entirely null in all 1,670,779 source rows.
- Do not apply statistical outlier clipping or filter vessel positions. Numeric extremes remain traceable for a later, domain-informed review.

## Six-file run summary

| Dataset | Input rows | Output rows | Cleaning result |
|---|---:|---:|---|
| AIS_POS | 19,014,229 | 18,973,676 | Removed 40,553 verified duplicate events; added cleaned COG/heading/ROT and message-aware SOG fields. |
| AIS_SPEC | 13,558,007 | 13,547,406 | Removed 10,601 exact whole-row duplicates; retained conflicting same-key reports; added ETA, draught, ship-type label, IMO-range, and position-fix interpretation fields. |
| AIS_ShipTypes | 256 | 256 | Normalized numeric code type; zero nulls and zero duplicate codes. |
| ERA5 | 537,510 | 537,510 | Complete time/grid rows retained; zero nulls in source fields. |
| CMEMS_PHY | 5,007,803 | 5,007,803 | Retained 402,038 nulls per variable, representing a fixed mask over 182 of 2,267 cells. |
| CMEMS_WAV | 1,670,779 | 1,670,779 | Retained fixed masks; dropped only the all-null `VMXL` field from the clean copy. |

Detailed null totals, invalid-code counts, and run row counts are in `data/processed/cleaning_summary.json`.

## Interpretation decisions and cautions

- AIS_POS 480,659 non-null COG values outside 0–359 and 10,138,994 non-null heading values outside 0–359 are null in the added clean fields; original codes are retained. ROT `-128` is null in its added clean field, while saturated `-127` and `+127` values remain. Four source readings are 102.199997 after float32 storage. AIS code 1,022 means 102.2 knots or higher, so these are upper-censored rather than ordinary measured speeds. In Message Type 27, SOG code 63 means unavailable ([US Coast Guard Message 27 specification](https://www.navcen.uscg.gov/sites/default/files/pdf/AIS/AIS_MSG27.pdf)). The cleaner adds separate flags for the two code rules and leaves both unavailable values blank in `sog_knots_clean`; source values remain unchanged. The Type 27 rule flags 22,106 raw reports; four reports use the upper-censored code.
- AIS_SPEC ETA components use valid ranges month 1–12, day 1–31, hour 0–23, minute 0–59 in the added fields. Zeros, unavailable codes (hour 24, minute 60), reserved codes, and structural nulls remain null there. The original component columns remain untouched.
- ETA component ranges are checked independently; the cleaning phase does not yet reject impossible calendar combinations (for example, 31 February), which should be handled only if ETA is used.
- Draught code 0 is marked unavailable and code 255 is marked as 25.5 m or greater; neither is treated as an exact measurement in `draught_m_clean`. Codes 1–254 convert from tenths of a metre. Dimension fields retain source codes because their upper values encode at-least bounds. `ShipType` remains numeric and gains a lookup label.
- Of 95 ship-type codes observed in AIS_SPEC, every code maps to the 256-row lookup; code 0 is explicitly “Not available.” The local IMO field has values as high as 982,381,900, outside the standard seven-digit IMO-number range. The dataset description explains values 10,000,000–1,073,741,823 as official flag-state numbers (40,675 rows); 3,256,209 rows fall in the standard IMO-number range, 1,024,540 use the unavailable code 0, and 14,292 fall in the unused 1–999,999 range. Thus the large values are valid coded field-state numbers, not corruption.
- The data paper specifies ERA5 `tcc` as a 0–1 fraction, but the local file is scaled 0–100 (maximum 100.00062). The clean copy retains `tcc` as supplied and adds `tcc_fraction_clean = tcc / 100`; only the tiny float32 overshoot at 100% is clipped to 1. The paper defines ERA5 pressure as hPa, temperatures as °C, winds as m/s; it defines CMEMS physics/wave units.
- The six source schemas store naive timestamps. For analysis, `Date_utc` and `time_utc` now label each timestamp as UTC without shifting the clock value. This is based on AIS report timestamps being UTC seconds and the UTC convention of the ERA5/CMEMS source products; timezone awareness was absent from the exported schemas.
- Wave direction clean fields map 360° to 0° using modulo 360, preserving original columns. Spatial-temporal joins and a model-ready sample are now created in the relationship/model notebooks. The current 20 km matched sample yields weak pooled positive speed correlations with wind speed (rho 0.134), current speed (0.124), and wave height (0.187). Five vessel-grouped splits show mean MAE 2.603 kn for the context model and 2.588 kn with environment (0.59% lower). These are retrospective results for the local quarter, not causal effects or evidence about fuel/emissions.

## Datatype and missingness policy

The Parquet storage schemas were inspected for all files: AIS_POS uses timestamp[ns], uint32 MMSI, int32 message type, bool position accuracy, float32 SOG/COG and float64 coordinates/status fields, strings and a list<string> provenance field; AIS_SPEC uses timestamp[ns], uint32 identifiers/coded numeric components, int32 message/type/fix codes and strings; the environmental tables use timestamps, floating coordinates/measures and WKB geometry; the lookup code is numeric. Timestamps remain timestamps; analysis copies also provide UTC-aware `Date_utc`/`time_utc` fields without changing the original clock values. Coordinates and scientific measures remain floating point; identifiers and encoded categories retain integer semantics; source strings and provenance lists remain available. New interpretation fields use nullable numeric values or Boolean flags. The CSV lookup code is normalized to nullable integer. Environmental geometry remains WKB and the geographic coordinates are preserved.

Missingness is not treated as a single cleanup defect: AIS fields are structurally absent in message types that do not carry them, AIS_SPEC static/voyage fields are absent in message 24 reports, and ocean/wave nulls follow fixed grid masks. No global row drop or imputation was performed.

## Track-jump screen and handoff

A great-circle haversine screen compared each vessel position with its immediately preceding MMSI position. Of 18,948,546 positive-time segments, 186,270 had gaps above six hours and were excluded from the primary short-gap screen. Among 18,762,276 segments with gaps up to six hours, median implied speed was 0.015 kn, p95 13.37 kn, p99 17.92 kn, and p99.9 23.75 kn. There were 5,874 segments above 30 kn, 81 above 40 kn across 69 MMSIs, and 5 above 60 kn; none exceeded 100 kn. The maximum was 71.76 kn. All 81 >40 kn candidates are exported with endpoints and reported SOG in `data/processed/track_jump_candidates.csv`. These are review flags only; no positions were dropped. A separate reported-speed screen found 495 cleaned reports above 40 kn across 195 MMSIs. Of these, 16 reports from four MMSIs exceed 40 kn while positions remain within 100 m over at least 24 hours. They are saved in `high_sog_stationary_position_candidates.csv` for review. The model excludes only exact candidate reports that occur in its matched sample (one report in the current sample); other fast craft observations are not removed by a blanket speed threshold.

The >60 kn candidates show clear implied-speed vs endpoint AIS SOG discrepancies (roughly 8.5–37.3 kn reported); they merit continued traceable exclusion/robustness review if track-derived features are used. The >40 kn threshold includes legitimate high-speed craft, so retain those observations unless analysis-specific evidence supports exclusion.

Temporal matching and variable definitions now have an evidence-based working convention documented above. The analysis notebooks now measure environmental match coverage, test exploratory speed associations, and compare context-only and environmental regression models. These outputs support retrospective screening only; they do not establish causes or fuel/emissions effects.
