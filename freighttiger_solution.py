
import csv
import json
import math
import hashlib
from pathlib import Path
from collections import defaultdict
from datetime import date, timedelta

# ============================================================
# FreightTiger Shipment Cost Analysis
# Uses Python standard library only.
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

SHIPMENTS_FILE = BASE_DIR / "shipment_records.csv"
NOTES_FILE = BASE_DIR / "context_notes.csv"
TEMPLATE_FILE = BASE_DIR / "sample_output_format_v2.csv"

OUTPUT_FILE = BASE_DIR / "output.csv"
README_FILE = BASE_DIR / "README.md"
COST_LOG_FILE = BASE_DIR / "run_cost_log.json"

OUTPUT_COLUMNS = [
    "route",
    "week_of",
    "cost_per_tonne_km",
    "vs_own_history",
    "vs_similar_routes",
    "flagged",
    "matched_note_id",
    "reason",
]

# Documented engineering assumptions; not thresholds supplied by the brief.
OWN_HISTORY_THRESHOLD = 0.10
PEER_THRESHOLD = 0.15
HISTORY_WEEKS = 8

# Conservative evidence windows:
# N001: flooding Feb 24-Mar 8, 2025.
# N002: festival surcharge during week beginning Jan 20, 2025.
# N003: nationwide diesel rise beginning May 5, 2025;
#       use only that calendar week because duration is unspecified.


def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path.name}")

    with path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise ValueError(f"No CSV header found in {path.name}")
        return reader.fieldnames, list(reader)


def parse_float(value, column, row_number):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"Invalid number in {column}, data row {row_number}: {value!r}"
        )

    if not math.isfinite(result):
        raise ValueError(
            f"Non-finite number in {column}, data row {row_number}"
        )

    return result


def monday_of(day):
    return day - timedelta(days=day.weekday())


def percent_change(current, baseline):
    if baseline is None or baseline == 0:
        return None
    return (current / baseline - 1.0) * 100.0


def format_percent(value):
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


def load_shipments():
    headers, rows = read_csv(SHIPMENTS_FILE)

    required = {
        "origin",
        "destination",
        "route_type",
        "quantity_tonnes",
        "distance_km",
        "freight_cost_inr",
        "shipment_date",
    }

    missing = required - set(headers)
    if missing:
        raise ValueError(
            "Missing shipment columns: " + ", ".join(sorted(missing))
        )

    groups = defaultdict(
        lambda: {
            "total_cost": 0.0,
            "total_tonne_km": 0.0,
            "shipment_count": 0,
            "route_type": None,
        }
    )

    for row_number, row in enumerate(rows, start=2):
        try:
            shipment_day = date.fromisoformat(row["shipment_date"].strip())
        except (ValueError, AttributeError):
            raise ValueError(
                f"Invalid shipment_date on CSV line {row_number}"
            )

        quantity = parse_float(
            row["quantity_tonnes"], "quantity_tonnes", row_number
        )
        distance = parse_float(
            row["distance_km"], "distance_km", row_number
        )
        cost = parse_float(
            row["freight_cost_inr"], "freight_cost_inr", row_number
        )

        if quantity <= 0 or distance <= 0 or cost < 0:
            raise ValueError(
                f"Invalid quantity, distance, or cost on CSV line {row_number}"
            )

        origin = row["origin"].strip()
        destination = row["destination"].strip()
        route = f"{origin}-{destination}"
        week = monday_of(shipment_day).isoformat()
        route_type = row["route_type"].strip()

        key = (route, week)
        group = groups[key]

        if group["route_type"] not in (None, route_type):
            raise ValueError(
                f"Inconsistent route_type for route {route}"
            )

        group["route_type"] = route_type
        group["total_cost"] += cost
        group["total_tonne_km"] += quantity * distance
        group["shipment_count"] += 1

    for key, group in groups.items():
        denominator = group["total_tonne_km"]
        if denominator <= 0:
            raise ValueError(f"Invalid tonne-km total for {key}")

        group["cost_per_tonne_km"] = group["total_cost"] / denominator

    return groups


def load_notes():
    headers, rows = read_csv(NOTES_FILE)

    required = {"note_id", "date", "applies_to", "note"}
    missing = required - set(headers)
    if missing:
        raise ValueError(
            "Missing context-note columns: " + ", ".join(sorted(missing))
        )

    notes = []
    for row_number, row in enumerate(rows, start=2):
        try:
            note_day = date.fromisoformat(row["date"].strip())
        except (ValueError, AttributeError):
            raise ValueError(
                f"Invalid note date on CSV line {row_number}"
            )

        notes.append({
            "note_id": row["note_id"].strip(),
            "date": note_day,
            "applies_to": row["applies_to"].strip(),
            "note": row["note"].strip(),
        })

    return notes


def applicable_increase_note(route, week_string, notes):
    """Return only a note whose route and documented time window fit."""
    week_start = date.fromisoformat(week_string)
    week_end = week_start + timedelta(days=6)

    allowed_windows = {
        "N001": (date(2025, 2, 24), date(2025, 3, 8)),
        "N002": (date(2025, 1, 20), date(2025, 1, 26)),
        "N003": (date(2025, 5, 5), date(2025, 5, 11)),
    }

    for note in sorted(notes, key=lambda item: item["note_id"]):
        if note["note_id"] not in allowed_windows:
            continue

        start, end = allowed_windows[note["note_id"]]
        if week_start > end or week_end < start:
            continue

        applies_to = note["applies_to"]

        if applies_to != "All Routes" and applies_to != route:
            continue

        return note

    return None


def analyze(groups, notes):
    by_route = defaultdict(list)
    by_week_type = defaultdict(list)

    for (route, week), group in groups.items():
        record = {
            "route": route,
            "week_of": week,
            "route_type": group["route_type"],
            "cost": group["cost_per_tonne_km"],
        }
        by_route[route].append(record)
        by_week_type[(week, group["route_type"])].append(record)

    for route in by_route:
        by_route[route].sort(key=lambda record: record["week_of"])

    output_rows = []

    for route in sorted(by_route):
        route_records = by_route[route]

        for index, current in enumerate(route_records):
            
            # Use only route records within the preceding eight calendar weeks.
            current_week = date.fromisoformat(current["week_of"])
            history_start = current_week - timedelta(weeks=HISTORY_WEEKS)

            previous = [
                record
                for record in route_records[:index]
                if history_start <= date.fromisoformat(record["week_of"]) < current_week
            ]

            if previous:
                own_baseline = sum(record["cost"] for record in previous) / len(previous)
            else:
                own_baseline = None


            peers = [
                record["cost"]
                for record in by_week_type[
                    (current["week_of"], current["route_type"])
                ]
                if record["route"] != route
            ]

            peer_baseline = sum(peers) / len(peers) if peers else None

            own_change = percent_change(current["cost"], own_baseline)
            peer_change = percent_change(current["cost"], peer_baseline)

            high_vs_history = (
                own_change is not None
                and own_change >= OWN_HISTORY_THRESHOLD * 100
            )
            high_vs_peers = (
                peer_change is not None
                and peer_change >= PEER_THRESHOLD * 100
            )

            anomaly = high_vs_history or high_vs_peers
            note = None

            if anomaly:
                note = applicable_increase_note(
                    route, current["week_of"], notes
                )

            if anomaly and note:
                flagged = "No (justified)"
                matched_note_id = note["note_id"]
                reason = (
                    f"Cost increase is supported by context note "
                    f"{note['note_id']} for {route} during the week "
                    f"of {current['week_of']}: {note['note']}"
                )
            elif anomaly:
                flagged = "Yes"
                matched_note_id = ""
                reasons = []

                if high_vs_history:
                    reasons.append(
                        f"{own_change:+.2f}% versus prior-route baseline"
                    )
                if high_vs_peers:
                    reasons.append(
                        f"{peer_change:+.2f}% versus same-week peer baseline"
                    )

                reason = (
                    "Unexplained cost increase: "
                    + "; ".join(reasons)
                    + ". No applicable context note supports this increase."
                )
            else:
                flagged = "No"
                matched_note_id = ""
                checks = []

                if own_change is None:
                    checks.append("own history unavailable")
                else:
                    checks.append(
                        f"own-history change {own_change:+.2f}%"
                    )

                if peer_change is None:
                    checks.append("peer comparison unavailable")
                else:
                    checks.append(
                        f"peer change {peer_change:+.2f}%"
                    )

                reason = (
                    "No configured anomaly threshold was crossed; "
                    + "; ".join(checks)
                    + "."
                )

            output_rows.append({
                "route": current["route"],
                "week_of": current["week_of"],
                "cost_per_tonne_km": f"{current['cost']:.6f}",
                "vs_own_history": format_percent(own_change),
                "vs_similar_routes": format_percent(peer_change),
                "flagged": flagged,
                "matched_note_id": matched_note_id,
                "reason": reason,
            })

    output_rows.sort(key=lambda row: (row["week_of"], row["route"]))
    return output_rows


def write_output(rows):
    with OUTPUT_FILE.open(
        "w", newline="", encoding="utf-8"
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=OUTPUT_COLUMNS,
            extrasaction="raise",
        )
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_readme():
    content = """# FreightTiger Shipment Cost Analysis

## Run

Requires Python 3.10+ and uses only the Python standard library.

```powershell
python freighttiger_solution.py
```

## Inputs

- `shipment_records.csv`
- `context_notes.csv`
- `sample_output_format_v2.csv`

## Output

`output.csv` follows the eight columns in the supplied template.

## Method

- Routes are directional origin-destination pairs.
- Shipments are grouped into Monday-to-Sunday calendar weeks.
- Weekly cost per tonne-km is total freight cost divided by the sum of
  quantity (tonnes) multiplied by distance (km) across the group.
- Own-history baseline is the arithmetic mean of available route-week
  values within the preceding eight calendar weeks. The current week
  is excluded. Missing weeks are not filled or imputed. When no prior
  route-week values exist in that window, the baseline is N/A.
- Peer baseline is the arithmetic mean of other routes' weekly
  cost-per-tonne-km values for the same calendar week and route type.
  The current route is excluded.
- Percent change is (current / baseline - 1) * 100.
- Flag if current cost is at least 10% above its own baseline OR at
  least 15% above its peer baseline. These thresholds are documented
  engineering assumptions because the brief supplies no numeric cutoffs.
- A threshold-crossing increase is marked `No (justified)` only if an
  increase-related note matches the route and the documented time window.
  Otherwise it is marked `Yes` (unexplained). Other rows are `No`.
- Notes with no explicit time duration are applied conservatively.
  N003 is limited to the week beginning 2025-05-05. N001 is limited to
  2025-02-24 through 2025-03-08; N002 to 2025-01-20 through 2025-01-26.
  Notes that do not support a cost increase are not used to justify one.
- This implementation uses deterministic rule-based evidence matching.
  It does not call an LLM or external API, so no generative model
  claims are made and model token usage is zero.

## Reproducibility

The script records the output SHA-256 and analysis counts in
`run_cost_log.json`. Run it three times and compare the reported
output SHA-256 values. Matching hashes indicate byte-identical output
for the same inputs and code.

## Limitations

The thresholds are explicit assumptions, not prescribed values.
Peer comparisons are unavailable when no other route of the same type
has a record in that calendar week. Context notes are matched
conservatively to avoid unsupported causal explanations.
"""
    README_FILE.write_text(content, encoding="utf-8")


def main():
    shipment_groups = load_shipments()
    notes = load_notes()
    output_rows = analyze(shipment_groups, notes)

    # Validate output schema against the supplied template.
    template_headers, _ = read_csv(TEMPLATE_FILE)
    if template_headers != OUTPUT_COLUMNS:
        raise ValueError(
            "Output columns do not match template: "
            f"{template_headers}"
        )

    write_output(output_rows)
    write_readme()

    flagged_count = sum(row["flagged"] == "Yes" for row in output_rows)
    justified_count = sum(
        row["flagged"] == "No (justified)" for row in output_rows
    )

    cost_log = {
        "input_shipment_records": sum(
            group["shipment_count"] for group in shipment_groups.values()
        ),
        "route_week_groups": len(shipment_groups),
        "output_rows": len(output_rows),
        "flagged_unexplained": flagged_count,
        "flagged_justified": justified_count,
        "llm_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_llm_cost_usd": 0.0,
        "llm_usage_note": (
            "No LLM/API calls were made; this is deterministic "
            "rule-based analysis."
        ),
        "output_sha256": sha256_file(OUTPUT_FILE),
    }

    COST_LOG_FILE.write_text(
        json.dumps(cost_log, indent=2) + "\n",
        encoding="utf-8",
    )

    print("Analysis completed successfully.")
    print(f"Shipment records: {cost_log['input_shipment_records']}")
    print(f"Route-week groups: {cost_log['route_week_groups']}")
    print(f"Output rows: {cost_log['output_rows']}")
    print(f"Unexplained flags: {flagged_count}")
    print(f"Justified flags: {justified_count}")
    print(f"Output file: {OUTPUT_FILE.name}")
    print(f"README: {README_FILE.name}")
    print(f"Cost/reproducibility log: {COST_LOG_FILE.name}")
    print(f"Output SHA-256: {cost_log['output_sha256']}")


if __name__ == "__main__":
    main()
