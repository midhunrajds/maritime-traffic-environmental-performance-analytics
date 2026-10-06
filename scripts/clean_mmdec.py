"""Create documented, derived clean copies of the six local MMDEC files.

Raw source files are read-only inputs. Outputs are written under data/processed.
"""
from pathlib import Path
import json
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Resolve paths relative to this script so callers can run it from any folder.
ROOT = Path(__file__).resolve().parents[1]
# Put derived files in their own directory so raw data remain unchanged.
OUT = ROOT / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)


def write_frame(frame, filename):
    """Write a pandas frame to compressed Parquet without a pandas index."""
    # Convert columns to Arrow types while excluding the incidental row index.
    table = pa.Table.from_pandas(frame, preserve_index=False)
    # Use Zstandard compression to reduce disk use for the derived clean copy.
    pq.write_table(table, OUT / filename, compression="zstd")


def clean_ais_positions():
    """Remove confirmed exact event repeats and add validity-aware fields."""
    # Read raw AIS positions; this operation does not alter the source file.
    frame = pd.read_parquet(ROOT / "Dataset_AIS_POS.parquet")
    original_rows = len(frame)
    # Add an explicit UTC interpretation without shifting the naive clock values.
    frame["Date_utc"] = frame["Date"].dt.tz_localize("UTC")
    # Audit compared all fields within repeated MMSI/time groups and found them
    # identical, so this key removes only confirmed exact duplicate events.
    frame = frame.drop_duplicates(subset=["Mmsi", "Date"], keep="first").copy()
    # Keep encoded source columns and put validated course values in a new field.
    frame["cog_deg_clean"] = frame["CourseOverGroundDegrees"].where(
        frame["CourseOverGroundDegrees"].between(0, 359)
    )
    # AIS heading code 511 means unavailable; all values outside 0..359 are null here.
    frame["heading_deg_clean"] = frame["TrueHeadingDegrees"].where(
        frame["TrueHeadingDegrees"].between(0, 359)
    )
    # ROT code -128 means no turn information; preserve saturated -127/+127 codes.
    frame["rate_of_turn_clean"] = frame["RateOfTurn"].mask(frame["RateOfTurn"].eq(-128))
    # Flag the AIS upper-censored SOG code, allowing for float32 storage rounding.
    frame["sog_upper_code_flag"] = frame["SpeedOverGround"].ge(102.19)
    # In long-range AIS Message 27, SOG code 63 means unavailable (USCG Message 27 specification).
    frame["sog_message27_unavailable_flag"] = frame["MessageType"].eq(27) & frame["SpeedOverGround"].eq(63)
    # Exclude both the Message 27 unavailable code and the ordinary AIS upper-censored SOG value.
    frame["sog_knots_clean"] = frame["SpeedOverGround"].where(
        ~(frame["sog_upper_code_flag"] | frame["sog_message27_unavailable_flag"])
    )
    # Flag latitude/longitude ranges without dropping otherwise traceable events.
    frame["position_valid_flag"] = frame["Latitude"].between(-90, 90) & frame[
        "Longitude"
    ].between(-180, 180)
    # Write the cleaned event table with original source columns intact.
    write_frame(frame, "ais_positions_clean.parquet")
    # Summarize the number of excluded codes and exact duplicate rows.
    return {
        "source_rows": original_rows,
        "output_rows": len(frame),
        "removed_confirmed_duplicate_rows": original_rows - len(frame),
        "cog_non_null_outside_0_359": int((frame["CourseOverGroundDegrees"].notna() & ~frame["CourseOverGroundDegrees"].between(0, 359)).sum()),
        "heading_non_null_outside_0_359": int((frame["TrueHeadingDegrees"].notna() & ~frame["TrueHeadingDegrees"].between(0, 359)).sum()),
        "rot_code_minus_128": int(frame["RateOfTurn"].eq(-128).sum()),
        "sog_at_or_above_102_2_flagged": int(frame["sog_upper_code_flag"].sum()),
        "message_27_sog_63_unavailable": int(frame["sog_message27_unavailable_flag"].sum()),
    }


def clean_ais_specifications():
    """Remove only exact whole-row repeats and add semantic AIS code fields."""
    # Read the AIS static/voyage message stream without modifying raw codes.
    frame = pd.read_parquet(ROOT / "Dataset_AIS_SPEC.parquet")
    original_rows = len(frame)
    # Label message times as UTC by AIS report-time convention without shifting them.
    frame["Date_utc"] = frame["Date"].dt.tz_localize("UTC")
    # Whole-row equality is required; conflicting same-key reports remain present.
    frame = frame.drop_duplicates(keep="first").copy()
    # Map valid ETA components and represent unavailable/reserved codes as null.
    frame["eta_month_clean"] = frame["EtaMonth"].where(frame["EtaMonth"].between(1, 12))
    frame["eta_day_clean"] = frame["EtaDay"].where(frame["EtaDay"].between(1, 31))
    frame["eta_hour_clean"] = frame["EtaHour"].where(frame["EtaHour"].between(0, 23))
    frame["eta_minute_clean"] = frame["EtaMinute"].where(frame["EtaMinute"].between(0, 59))
    # Code zero means unavailable; code 255 means 25.5 m or greater, not an exact value.
    frame["draught_unavailable_flag"] = frame["Draught10thMetres"].eq(0)
    frame["draught_upper_code_flag"] = frame["Draught10thMetres"].eq(255)
    # Convert only exact reported draughts (0.1–25.4 m) from tenths of a metre.
    frame["draught_m_clean"] = (frame["Draught10thMetres"] / 10).where(
        frame["Draught10thMetres"].between(1, 254)
    )
    # Add labels from the local semicolon-separated lookup, retaining numeric codes.
    lookup = pd.read_csv(ROOT / "Dataset_AIS_ShipTypes.csv", sep=";")
    frame["ship_type_label"] = frame["ShipType"].map(lookup.set_index("Code")["Type"])
    # Separate valid IMO numbers from AIS official flag-state numbers and sentinels.
    frame["imo_unavailable_flag"] = frame["ImoNumber"].eq(0)
    frame["imo_number_valid_flag"] = frame["ImoNumber"].between(1_000_000, 9_999_999)
    frame["imo_flag_state_number_flag"] = frame["ImoNumber"].between(10_000_000, 1_073_741_823)
    frame["imo_reserved_or_invalid_flag"] = (frame["ImoNumber"].gt(0) & ~frame["imo_number_valid_flag"] & ~frame["imo_flag_state_number_flag"])
    # Codes 9..14 are reserved; code 15 is a valid internal GNSS fix type.
    frame["position_fix_code_valid_flag"] = frame["PositionFixType"].isin(
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 15]
    )
    # Trim fixed-width AIS strings and map all-at-sign unavailable markers to null.
    for column in ["CallSign", "VesselName", "Destination"]:
        cleaned = frame[column].astype("string").str.strip()
        frame[f"{column}_clean"] = cleaned.mask(cleaned.str.fullmatch(r"@+", na=False))
    # Preserve dimension fields as coded because maxima are upper-censoring values.
    write_frame(frame, "ais_specifications_clean.parquet")
    # Report validity outcomes without dropping the flagged source records.
    return {
        "source_rows": original_rows,
        "output_rows": len(frame),
        "removed_exact_whole_row_duplicates": original_rows - len(frame),
        "imo_unavailable_code_rows": int(frame["imo_unavailable_flag"].sum()),
        "imo_standard_number_rows": int(frame["imo_number_valid_flag"].sum()),
        "imo_flag_state_number_rows": int(frame["imo_flag_state_number_flag"].sum()),
        "imo_reserved_or_invalid_code_rows": int(frame["imo_reserved_or_invalid_flag"].sum()),
        "draught_unavailable_code_rows": int(frame["draught_unavailable_flag"].sum()),
        "draught_upper_limit_code_rows": int(frame["draught_upper_code_flag"].sum()),
        "eta_month_missing_or_invalid": int(frame["eta_month_clean"].isna().sum()),
        "eta_day_missing_or_invalid": int(frame["eta_day_clean"].isna().sum()),
        "eta_hour_missing_or_invalid": int(frame["eta_hour_clean"].isna().sum()),
        "eta_minute_missing_or_invalid": int(frame["eta_minute_clean"].isna().sum()),
        "unmapped_ship_type_rows": int((frame["ShipType"].notna() & frame["ship_type_label"].isna()).sum()),
    }


def clean_ship_type_lookup():
    """Normalize and copy the type-code reference table."""
    # Parse the source using its observed semicolon delimiter.
    frame = pd.read_csv(ROOT / "Dataset_AIS_ShipTypes.csv", sep=";")
    # Require numeric codes and store them as a nullable integer column.
    frame["Code"] = pd.to_numeric(frame["Code"], errors="raise").astype("Int64")
    # Save a normalized copy while preserving the original labels and descriptions.
    frame.to_csv(OUT / "ais_ship_types_clean.csv", index=False)
    # Summarize key integrity for the reproducible run record.
    return {"rows": len(frame), "null_cells": int(frame.isna().sum().sum()),
            "duplicate_codes": int(frame["Code"].duplicated().sum())}


def copy_environmental_file(filename, output_name, all_null_columns=()):
    """Copy a grid, retaining null masks and removing only audited all-null fields."""
    # Load one environmental file at a time to limit memory use.
    frame = pd.read_parquet(ROOT / filename)
    # Capture null counts before the optional schema cleanup for the audit record.
    null_counts = frame.isna().sum().to_dict()
    # Add a timezone-aware UTC interpretation without shifting naive clock values.
    frame["time_utc"] = frame["time"].dt.tz_localize("UTC")
    # Normalize local percent-scaled cloud cover to the documented 0-to-1 fraction.
    if filename == "Dataset_ERA5.parquet":
        fraction = frame["tcc"] / 100.0
        # Correct only the tiny float32 overshoot at the 100-percent ceiling.
        fraction = fraction.mask(fraction.gt(1) & fraction.le(1.00001), 1.0)
        # Keep the source field and add a standardized fractional cloud-cover field.
        frame["tcc_fraction_clean"] = fraction.where(fraction.between(0, 1))
    # Normalize circular directions so an encoded 360 degrees becomes zero degrees.
    if filename == "Dataset_CMEMS_WAV.parquet":
        for column in ["VMDR", "VMDR_SW1", "VMDR_SW2", "VMDR_WW", "VPED"]:
            frame[f"{column}_deg_clean"] = frame[column].mod(360)
    # Drop a field only if it is still completely null, as established in the audit.
    for column in all_null_columns:
        if column in frame and frame[column].notna().any():
            raise ValueError(f"Expected all-null field {column} contains values")
        frame = frame.drop(columns=[column], errors="ignore")
    # Retain fixed geographic masks; never impute null environmental observations.
    write_frame(frame, output_name)
    # Include source null totals and output row count in the cleaning summary.
    result = {"rows": len(frame), "source_null_counts": null_counts}
    # Record source and normalized ERA5 cloud-cover ranges for auditability.
    if filename == "Dataset_ERA5.parquet":
        result["tcc_source_min"] = float(frame["tcc"].min())
        result["tcc_source_max"] = float(frame["tcc"].max())
        result["tcc_fraction_clean_min"] = float(frame["tcc_fraction_clean"].min())
        result["tcc_fraction_clean_max"] = float(frame["tcc_fraction_clean"].max())
    # Return source null totals and any normalization metrics to the run summary.
    return result


def main():
    """Run all six cleaning steps and save a compact reproducibility record."""
    # Clean movement, vessel specifications, and the categorical lookup.
    summary = {
        "AIS_POS": clean_ais_positions(),
        "AIS_SPEC": clean_ais_specifications(),
        "AIS_ShipTypes": clean_ship_type_lookup(),
    }
    # Preserve weather/ocean masks and remove the WAV field known to be fully null.
    summary["ERA5"] = copy_environmental_file("Dataset_ERA5.parquet", "era5_clean.parquet")
    summary["CMEMS_PHY"] = copy_environmental_file("Dataset_CMEMS_PHY.parquet", "cmems_phy_clean.parquet")
    summary["CMEMS_WAV"] = copy_environmental_file(
        "Dataset_CMEMS_WAV.parquet", "cmems_wav_clean.parquet", all_null_columns=("VMXL",)
    )
    # Save machine-readable counts, decisions, and source null totals.
    (OUT / "cleaning_summary.json").write_text(json.dumps(summary, indent=2))
    # Print findings so an interactive notebook or terminal run shows the audit.
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
