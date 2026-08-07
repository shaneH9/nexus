# Historical Market Data Guide

Milestone L supports offline Databento historical MBO CSV files. It does not
download data, call a provider API, or require credentials. Databento documents
the format in its [MBO schema](https://databento.com/docs/schemas-and-data-formats/mbo),
[common action/flag conventions](https://databento.com/docs/standards-and-conventions/common-fields-enums-types),
and [MBO snapshot convention](https://databento.com/docs/standards-and-conventions/mbo-snapshot).

## Supply a real file

1. Acquire a historical MBO (`schema=mbo`) batch file in CSV encoding directly
   from Databento under the applicable provider/exchange license. Enable the
   batch option to add a symbol field; the adapter requires it for explicit,
   auditable symbology mapping.
2. Keep it outside Git, preferably under ignored `historical_data/`.
3. Preserve these columns exactly:

   ```text
   ts_recv, ts_event, rtype, publisher_id, instrument_id, action, side,
   price, size, channel_id, order_id, flags, ts_in_delta, sequence, symbol
   ```

4. Declare `PRETTY_DECIMAL` for pretty CSV prices or
   `FIXED_1E9_INTEGER` for raw integer prices.
5. Add an explicit mapping for each
   `(publisher_id, provider_instrument_id, provider_symbol, venue)` to one
   canonical `Instrument`, including tick size and optional UTC effective dates.
6. Configure the IANA exchange timezone and session cutoffs. Timestamps must be
   UTC-aware ISO-8601 text or integer Unix nanoseconds.
7. For provider recovery snapshots, set
   `snapshot_session_date_offset_days` so the snapshot's receive-date maps to
   the intended exchange-local trading date. Databento UTC-day snapshots for a
   later U.S. trading date commonly require explicit review and may require `1`;
   do not assume this without inspecting the acquired file.

Do not commit the file or provider credentials.

## Establish exact source identity

Calculate the hash and byte count before finalizing the experiment manifest:

```bash
python -c "from pathlib import Path; from sra_nexus.market_data.providers.databento.adapter import sha256_file; print(sha256_file(Path('historical_data/file.csv')))"
```

Copy `source_filename`, `sha256`, and `byte_count` into `expected_files`. The
runner refuses any mismatch.

## Configure an experiment

Copy [the fixture experiment](../examples/historical/fixture_experiment.json)
and change only preregistered inputs before examining labels:

- source path and expected SHA-256/size;
- dataset/schema and instrument/venue mapping;
- half-open UTC research range and session scope;
- warm-up count and known halt/corporate-action boundaries;
- frozen aggression-episode event-gap, exchange-gap, and maximum-span policy;
- frozen SRA, label, purge, and embargo configurations;
- each named permutation configuration and seed;
- every primary, secondary, and baseline hypothesis;
- output directory and limitations.

The current runner accepts one uniform provider/schema/normalization/process-time
policy per experiment. Files must be in chronological non-reopening session
order. Multi-instrument interleaving inside one CSV is not yet supported by the
session-batch runner; use separately scoped experiments/files until a streaming
merge layer is added.

## Inspect without research

```bash
python -m sra_nexus.research.run \
  --experiment path/to/experiment.json \
  --dry-run
```

Dry run parses and validates the immutable experiment, hashes every source,
performs strict pre-flight inspection, verifies expected identities, lists all
declared hypotheses, and estimates normalized events. It does not replay,
construct labels, or run permutations.

For programmatic inspection, construct `DatabentoMboCsvAdapter(config)` and call
`inspect()`. No record is repaired.

## Run

```bash
python -m sra_nexus.research.run \
  --experiment path/to/experiment.json
```

`--output-root /an/alternate/local/path` may relocate artifacts without changing
the semantic `ExperimentHash`.

Success prints the immutable `research_runs/<experiment_hash>/` path and
operational event/observation/runtime metrics. Validation or research failure
returns nonzero. Existing output is reused only if every deterministic artifact
is byte-identical.

The result is gross historical statistical evidence. It is not a trading
signal, execution instruction, alpha model, cost estimate, allocation, or live
brokerage workflow.
