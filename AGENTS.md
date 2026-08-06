# SRA-Nexus Development Guide

## Project Purpose

SRA-Nexus is an event-driven quantitative trading research platform combining:

1. world, business, and news aggregation;
2. canonical event construction and entity/instrument mapping;
3. order-book microstructure research;
4. Shock-Resiliency Asymmetry (SRA);
5. statistical alpha estimation;
6. portfolio and risk optimization;
7. execution simulation; and
8. historical event replay.

## Important Safety and Research Rule

SRA-Nexus is a research and backtesting system first. Do not build functionality
that blindly submits live brokerage orders. Live execution must remain isolated
behind an explicit future adapter and disabled by default.

## Architectural Rules

Maintain strict separation between the following stages:

`Observe -> Interpret -> Predict -> Allocate -> Execute`

- The news aggregator must never place trades.
- The SRA engine must never size positions.
- The alpha model estimates return distributions, not `BUY`/`SELL` commands.
- The portfolio optimizer allocates capital but does not invent alpha.
- The execution engine handles order mechanics but does not decide whether a
  signal is valid.
- The risk engine can veto all other modules.

Dependencies should follow this separation and must not introduce hidden
decision-making across stage boundaries.

## Time Rules

Every external event should preserve:

- `event_time`: when the event occurred at its source;
- `receive_time`: when SRA-Nexus received the event; and
- `process_time`: when SRA-Nexus processed the event.

Use UTC internally. Historical replay must never expose information before its
`receive_time` and `process_time` permit it. Tests for replay and time-dependent
features must explicitly guard against look-ahead bias.

## Data Rules

- Raw source data must be immutable.
- Store derived and normalized data separately from raw data.
- Make all transformations reproducible.
- Do not use ticker strings as primary internal identifiers; use
  `instrument_id`.
- Prefer typed dataclasses or Pydantic models where appropriate.
- Use UTC internally for all timestamps.

## Engineering Rules

- Support Python 3.12 and newer.
- Use type hints throughout.
- Use pytest for tests.
- Use Ruff for linting and formatting.
- Write clear docstrings.
- Keep functions small and cohesive.
- Do not create giant god classes.
- Avoid premature machine learning.
- Prefer deterministic implementations before sophisticated models.
- Give every mathematical feature focused unit tests.
- State units in financial calculations, APIs, variable names, and docstrings.
- Avoid floating-point money representations where exact monetary accounting
  matters; prefer `decimal.Decimal` or integer minor units.
- Use configuration instead of hard-coded API keys.
- Read secrets from environment variables. Never commit `.env` files.

## Package Responsibilities

- `common`: shared primitives with no domain-stage decision logic.
- `reference`: canonical entities, instruments, and identifier mapping.
- `aggregator`: observation and source ingestion only.
- `market_data`: normalized market and order-book observations.
- `sra`: Shock-Resiliency Asymmetry research features.
- `regimes`: market and event regime classification.
- `alpha`: statistical return-distribution estimation.
- `portfolio`: capital allocation from externally supplied estimates.
- `execution`: simulated order mechanics and future isolated adapters.
- `risk`: limits, validation, and vetoes across the system.
- `backtest`: time-safe historical replay and experiment orchestration.
- `storage`: immutable raw and separate derived-data persistence interfaces.
- `monitoring`: research-run observability and diagnostics.

## Development Checks

Before completing a change, run:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
```
