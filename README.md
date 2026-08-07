# SRA-Nexus

SRA-Nexus is an event-driven quantitative trading research platform for studying
how real-world events propagate through markets. It is a research and backtesting
system first: no component submits live brokerage orders. Any future live adapter
must be isolated, explicit, and disabled by default.

## Architecture

The system keeps decision responsibilities separated:

```text
Observe -> Interpret -> Predict -> Allocate -> Execute
```

- **Observe:** `aggregator` and `market_data` acquire source observations.
- **Interpret:** `reference`, `sra`, and `regimes` construct canonical context
  and deterministic features.
- **Predict:** `alpha` estimates return distributions rather than trade commands.
- **Allocate:** `portfolio` converts supplied estimates into constrained capital
  allocations.
- **Execute:** `execution` simulates order mechanics without validating alpha.

The `risk` package may veto any stage. `backtest` provides time-safe historical
replay, `storage` separates immutable raw data from reproducible derived data,
and `monitoring` provides diagnostics. External observations preserve
`event_time`, `receive_time`, and `process_time`, all in UTC.

Historical empirical research is provider-isolated and preregistered. The first
offline adapter accepts strict Databento MBO CSV, verifies source SHA-256, replays
canonical events through the existing SRA pipeline, uses chronological
walk-forward folds and block permutation, and writes deterministic JSON/Markdown
evidence. It does not tune SRA, fit alpha, allocate capital, estimate costs, or
trade. See [the historical data guide](docs/historical-data.md).

Fixture dry run and full execution:

```bash
python -m sra_nexus.research.run \
  --experiment examples/historical/fixture_experiment.json \
  --dry-run
python -m sra_nexus.research.run \
  --experiment examples/historical/fixture_experiment.json
```

## Repository Layout

```text
src/sra_nexus/
  common/       reference/     aggregator/sources/
  market_data/  sra/           regimes/             alpha/
  portfolio/    execution/     risk/                backtest/
  storage/      monitoring/
tests/
docs/
```

## Development

Python 3.12 or newer is required. Install the package and development tools with
`python -m pip install -e '.[dev]'`. The standard checks are exactly:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy src tests
```

See `AGENTS.md` for the full architectural, timing, data, safety, and engineering
rules.
