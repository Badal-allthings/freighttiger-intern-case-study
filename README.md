# FreightTiger Shipment Cost Analysis

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
