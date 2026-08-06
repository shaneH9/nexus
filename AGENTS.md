# AGENTS.md

# SRA-Nexus Repository Instructions

## Project Purpose

SRA-Nexus is an event-driven quantitative trading research platform combining:

1. World, business, macroeconomic, regulatory, and company-news aggregation.
2. Canonical event construction and entity/instrument mapping.
3. Limit-order-book microstructure research.
4. Shock-Resiliency Asymmetry (SRA).
5. Statistical alpha estimation.
6. Portfolio and risk optimization.
7. Execution simulation.
8. Historical event replay and walk-forward evaluation.

The project is research- and backtesting-first.

Do not build functionality that blindly submits live brokerage orders. Any future live execution adapter must be isolated, explicitly enabled, and disabled by default.

---

## Core Architecture Rule

Maintain strict separation between:

Observe -> Interpret -> Predict -> Allocate -> Execute

Responsibilities:

* The news/event aggregator observes and structures external information.
* The SRA engine interprets order-book behavior.
* The alpha layer estimates future return distributions.
* The portfolio optimizer allocates capital based on alpha and risk.
* The execution layer determines how target positions could be obtained.
* The risk engine may veto any other component.

Do not allow modules to silently take over another module's responsibility.

In particular:

* News does not directly create orders.
* SRA does not size positions.
* Alpha models do not directly emit exchange orders.
* Portfolio optimization does not invent expected returns.
* Execution logic does not decide whether an alpha hypothesis is valid.
* Risk controls override all trading decisions.

---

## Time and Look-Ahead Rules

Every externally sourced event should preserve three timestamps where applicable:

* `event_time`: when the event occurred or was published at the source.
* `receive_time`: when our infrastructure received it.
* `process_time`: when it became usable by our system.

Use timezone-aware UTC datetimes internally.

Historical replay must never expose information before it could realistically have been available.

An event published at 10:00:00.000 but received at 10:00:00.350 cannot be used by a simulated strategy at 10:00:00.000.

Avoid look-ahead bias in all features, labels, news states, portfolio states, and execution assumptions.

---

## Data Architecture

Maintain separate data layers.

### Immutable Raw Layer

Examples:

* raw news
* raw SEC filings
* raw macro releases
* raw market-data events
* raw reference data

Raw source records should not be overwritten after ingestion.

### Normalized Layer

Examples:

* canonical events
* normalized book events
* entities
* instruments
* event exposures

### Derived Feature Layer

Examples:

* news states
* order-book snapshots
* liquidity shocks
* shock responses
* shock pairs
* SRA states
* market regimes

### Model Layer

Examples:

* alpha predictions
* risk estimates
* optimization outputs
* fill predictions

### Trading Layer

Examples:

* simulated orders
* fills
* positions
* portfolio snapshots
* risk events

Derived data should be reproducible from lower-level data whenever practical.

---

## Identifier Rules

Do not use ticker strings as primary internal identifiers.

Use stable typed identifiers such as:

* `instrument_id`
* `entity_id`
* `event_id`
* `news_id`
* `shock_id`

Ticker symbols are metadata and may change or collide across venues.

---

## Engineering Standards

Use Python 3.12 or newer.

Use:

* type hints throughout
* `pytest`
* Ruff for linting and formatting
* clear docstrings
* small focused functions
* explicit interfaces/protocols between modules
* deterministic implementations before sophisticated ML
* configuration files/environment variables instead of hard-coded secrets

Prefer typed dataclasses or Pydantic models where validation is valuable.

Avoid giant "god" classes.

Do not introduce machine learning when a deterministic implementation is sufficient for the current milestone.

Do not optimize code prematurely at the cost of clarity.

---

## Financial Calculation Rules

Every mathematical feature should document:

* definition
* expected range
* units
* required inputs
* edge cases

Every mathematical feature should have unit tests.

Examples include:

* order-book imbalance
* weighted depth
* shock intensity
* price impact
* replenishment ratio
* recovery time
* net liquidity provision
* aggressor effectiveness
* portfolio exposure
* transaction costs

Avoid floating-point representations for exact monetary accounting where precision matters.

Research features expressed as normalized ratios may use floating point when appropriate.

---

## Secrets

Never commit:

* API keys
* passwords
* brokerage credentials
* database credentials
* private tokens

Load secrets from environment variables.

`.env` must be ignored by Git.

Provide `.env.example` with placeholder variable names only.

---

## Testing Philosophy

Software correctness tests and research hypothesis tests are different.

### Software tests

Verify implementation correctness.

Examples:

* validation rejects invalid score ranges
* replenishment ratio computes correctly
* duplicate raw news records are not inserted
* an `as_of` query never returns future information

### Research tests

Evaluate whether a feature contains predictive information.

Examples:

* whether declining aggressor effectiveness predicts reversal
* whether news uncertainty weakens SRA signals
* whether event-aware portfolio risk improves tail behavior

Do not confuse backtest profitability with unit-test correctness.

---

## Repository Structure

Use approximately:

```text
src/sra_nexus/
    common/
    reference/
    aggregator/
        sources/
    market_data/
    sra/
    regimes/
    alpha/
    portfolio/
    execution/
    risk/
    backtest/
    storage/
    monitoring/

tests/
    unit/
    integration/
    fixtures/
    regression/

docs/
    decisions/
```

---

## Aggregator Rules

The news/event aggregator is the first major implementation target.

The core pipeline is:

```text
Raw provider input
    ->
RawNewsItem
    ->
Normalization
    ->
CanonicalEvent
    ->
Entity linking
    ->
Instrument mapping
    ->
Event exposure
    ->
NewsState
```

The aggregator must never directly place or recommend trades.

Provider-specific fields should remain in `raw_metadata` rather than leaking into canonical models.

The rest of SRA-Nexus should not need to know which provider supplied an event.

An important eventual interface is:

```python
get_news_state(
    instrument_id=...,
    as_of=...,
)
```

The `as_of` timestamp is mandatory for historical reproducibility.

---

## Initial Canonical Objects

The first core domain objects are:

* `Instrument`
* `Entity`
* `RawNewsItem`
* `CanonicalEvent`
* `EventExposure`
* `NewsState`

Later objects include:

* `BookEvent`
* `BookSnapshot`
* `LiquidityShock`
* `ShockPair`
* `SRAState`
* `AlphaOutput`
* `PortfolioState`
* `ExecutionIntent`

---

## Development Order

Follow this order unless there is a strong documented reason to deviate:

1. Repository scaffold and domain contracts.
2. Aggregator raw ingestion.
3. Local storage and historical `as_of` reconstruction.
4. Canonical event generation.
5. Entity/instrument linking.
6. Event exposures and `NewsState`.
7. Market-data ingestion and book reconstruction.
8. Shock/resiliency research.
9. Second-shock and toxicity research.
10. Alpha modeling.
11. Portfolio optimization.
12. Execution simulation.
13. Integrated historical replay.
14. Live paper system.
15. Only then consider isolated live execution adapters.

---

## Architecture Decisions

Record significant architectural choices under:

```text
docs/decisions/
```

Examples:

* UTC-only internal timestamps
* immutable raw data
* SQLite for initial development storage
* canonical-event model
* choice of NLP/embedding provider
* covariance estimator
* execution simulator assumptions

Architecture decisions should explain:

1. Context
2. Decision
3. Alternatives considered
4. Consequences

---

## Before Completing a Coding Task

Always:

1. Read this file.
2. Read `docs/architecture.md` for relevant system definitions.
3. Preserve module boundaries.
4. Add or update tests.
5. Run the standard development checks:

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy src tests
```

6. Report assumptions and unresolved issues.
7. Do not silently change mathematical definitions in `docs/architecture.md`.

If implementation reveals that the architecture should change, propose the change explicitly rather than quietly diverging from the specification.
