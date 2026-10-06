"""Screen consecutive AIS positions for implausible implied vessel speeds."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

# Find the project root so this script is independent of the terminal directory.
ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "data" / "processed" / "ais_positions_clean.parquet"
OUT = ROOT / "data" / "processed"

# Read only columns needed for a first-pass movement plausibility screen.
positions = pd.read_parquet(
    CLEAN, columns=["Mmsi", "Date", "MessageType", "Latitude", "Longitude",
                    "SpeedOverGround", "sog_knots_clean", "sog_message27_unavailable_flag"]
)
# Sort each vessel's observations chronologically before comparing adjacent points.
positions = positions.sort_values(["Mmsi", "Date"], kind="mergesort").reset_index(drop=True)
# Calculate the previous observation for each MMSI to form consecutive track segments.
groups = positions.groupby("Mmsi", sort=False)
positions["previous_time"] = groups["Date"].shift(1)
positions["previous_latitude"] = groups["Latitude"].shift(1)
positions["previous_longitude"] = groups["Longitude"].shift(1)
# Convert the time difference to hours so distance/time yields knots after conversion.
positions["gap_hours"] = (positions["Date"] - positions["previous_time"]).dt.total_seconds() / 3600
# Convert point coordinates to radians for the haversine great-circle calculation.
lat1 = np.radians(positions["previous_latitude"].to_numpy())
lat2 = np.radians(positions["Latitude"].to_numpy())
lon1 = np.radians(positions["previous_longitude"].to_numpy())
lon2 = np.radians(positions["Longitude"].to_numpy())
# Calculate the central angle while protecting floating-point rounding at the limits.
dlat = lat2 - lat1
dlon = lon2 - lon1
a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
a = np.clip(a, 0, 1)
# Convert great-circle distance from Earth-radius kilometres to nautical miles.
positions["segment_distance_nm"] = 2 * 6371.0088 * np.arcsin(np.sqrt(a)) / 1.852
# Compute implied speed only for positive gaps; equal-time reports are not movement segments.
positions["implied_speed_knots"] = positions["segment_distance_nm"] / positions["gap_hours"]
# Limit primary screening to intervals of at most six hours to avoid treating long gaps as tracks.
short_segments = positions["gap_hours"].gt(0) & positions["gap_hours"].le(6)
# Flag high-end candidate segments above 40 knots for manual/domain review.
positions["over_40_knots_flag"] = short_segments & positions["implied_speed_knots"].gt(40)
# Mark a more severe subset above 60 knots without deleting any vessel observations.
positions["over_60_knots_flag"] = short_segments & positions["implied_speed_knots"].gt(60)
# Add a more extreme threshold that is physically implausible for ordinary vessel traffic.
positions["over_100_knots_flag"] = short_segments & positions["implied_speed_knots"].gt(100)
# Capture the previous/current point and reported speed for flagged segment investigation.
flagged = positions.loc[
    positions["over_40_knots_flag"],
    ["Mmsi", "previous_time", "Date", "gap_hours", "previous_latitude",
     "previous_longitude", "Latitude", "Longitude", "segment_distance_nm",
     "implied_speed_knots", "MessageType", "SpeedOverGround", "sog_knots_clean",
     "sog_message27_unavailable_flag", "over_60_knots_flag", "over_100_knots_flag"],
]
# Save flagged pairs as a CSV so location/time anomalies can be inspected before exclusion.
flagged.to_csv(OUT / "track_jump_candidates.csv", index=False)
# Find vessels that repeatedly report >40 kn while their reported positions remain in one tiny area.
high_reported_speed = positions.loc[positions["sog_knots_clean"].gt(40)].copy()
# Review each vessel's high-speed reports for a repeated, nearly fixed position over multiple days.
stationary_position_candidate_groups = []
for mmsi, vessel_rows in high_reported_speed.groupby("Mmsi", sort=False):
    # Require several reports spread over time so one isolated fix does not trigger this screen.
    if len(vessel_rows) < 3 or (vessel_rows["Date"].max() - vessel_rows["Date"].min()).total_seconds() < 24 * 3600:
        continue
    # Estimate the spread in metres around the vessel's mean reported location.
    mean_latitude = vessel_rows["Latitude"].mean()
    mean_longitude = vessel_rows["Longitude"].mean()
    latitude_spread_m = (vessel_rows["Latitude"] - mean_latitude) * 111_320
    longitude_spread_m = (vessel_rows["Longitude"] - mean_longitude) * 111_320 * np.cos(np.radians(mean_latitude))
    # Use the greatest radial distance as a simple position-spread measure.
    position_spread_m = np.sqrt(latitude_spread_m ** 2 + longitude_spread_m ** 2)
    # Mark only groups whose high-speed reports stay within a 100-metre circle.
    if position_spread_m.max() <= 100:
        vessel_rows = vessel_rows.copy()
        vessel_rows["position_spread_from_mean_m"] = position_spread_m
        stationary_position_candidate_groups.append(vessel_rows)
# Combine any candidates while retaining all reported fields for a manual review.
if stationary_position_candidate_groups:
    stationary_position_candidates = pd.concat(stationary_position_candidate_groups, ignore_index=True)
else:
    stationary_position_candidates = high_reported_speed.head(0).copy()
# Save these as review candidates; they are not removed from the cleaned AIS data.
stationary_position_candidates.to_csv(OUT / "high_sog_stationary_position_candidates.csv", index=False)
# Build summaries for all adjacent intervals and the short-gap population.
valid_segments = positions["gap_hours"].gt(0)
summary = {
    "input_position_rows": int(len(positions)),
    "positive_time_segments": int(valid_segments.sum()),
    "segments_gap_over_six_hours": int(positions["gap_hours"].gt(6).sum()),
    "short_segments_up_to_six_hours": int(short_segments.sum()),
    "short_segment_speed_quantiles_knots": positions.loc[short_segments, "implied_speed_knots"]
        .quantile([0.5, 0.9, 0.95, 0.99, 0.999]).to_dict(),
    "short_segments_over_30_knots": int((short_segments & positions["implied_speed_knots"].gt(30)).sum()),
    "short_segments_over_40_knots": int(positions["over_40_knots_flag"].sum()),
    "short_segments_over_60_knots": int(positions["over_60_knots_flag"].sum()),
    "distinct_mmsi_with_over_40_knots": int(positions.loc[positions["over_40_knots_flag"], "Mmsi"].nunique()),
    "short_segments_over_100_knots": int(positions["over_100_knots_flag"].sum()),
    "max_short_segment_speed_knots": float(positions.loc[short_segments, "implied_speed_knots"].max()),
    "reported_sog_over_40_rows": int(high_reported_speed.shape[0]),
    "high_sog_stationary_position_candidate_rows": int(len(stationary_position_candidates)),
    "high_sog_stationary_position_candidate_mmsi": int(stationary_position_candidates["Mmsi"].nunique()),
    "high_sog_stationary_candidate_rule": "At least 3 measured SOG reports above 40 kn spanning at least 24 hours, all within 100 m of their mean position; review flag only.",
    "interpretation": "Movement and reported-speed inconsistencies are review candidates only; no vessel positions are removed from the cleaned file.",
}
# Persist numeric results in JSON for the cleaning and methods records.
(OUT / "trajectory_screen_summary.json").write_text(json.dumps(summary, indent=2))
# Print the summary when run from terminal or a notebook.
print(json.dumps(summary, indent=2))
