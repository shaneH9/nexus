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
