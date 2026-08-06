# SRA-Nexus Architecture

## 1. Objective

SRA-Nexus is an event-driven quantitative trading research system designed to combine:

* real-time company/business/world/macro news
* structured event intelligence
* limit-order-book microstructure
* Shock-Resiliency Asymmetry
* statistical expected-return estimation
* portfolio optimization
* event-aware risk management
* realistic execution simulation

The central research hypothesis is:

> When similarly directed aggressive liquidity shocks occur repeatedly, changes in the market's response to those shocks may reveal whether the aggressor is gaining or losing effectiveness.

Example:

Repeated aggressive selling occurs.

If later selling causes:

* less downward price impact
* faster bid replenishment
* broader multi-level bid recovery
* greater executed-liquidity credibility
* increased ask withdrawal
* declining directional-flow toxicity

then sellers may be losing control of the auction.

The bearish case is symmetric.

The system does not assume this hypothesis is true. It must be tested and falsified out of sample.

---

# 2. System Layers

The architecture follows:

```text
OBSERVE
    |
    v
INTERPRET
    |
    v
PREDICT
    |
    v
ALLOCATE
    |
    v
EXECUTE
```

More concretely:

```text
NEWS / OFFICIAL DATA
        |
        v
EVENT AGGREGATOR
        |
        v
EVENT STATE
        |
        +----------------------+
                               |
MARKET DATA                    |
        |                      |
        v                      |
ORDER BOOK RECONSTRUCTION      |
        |                      |
        v                      |
SRA MICROSTRUCTURE ENGINE      |
        |                      |
        +----------+-----------+
                   |
                   v
              ALPHA MODEL
                   |
                   v
       EXPECTED RETURN DISTRIBUTION
                   |
                   v
        PORTFOLIO / RISK OPTIMIZER
                   |
                   v
             TARGET POSITIONS
                   |
                   v
            EXECUTION ENGINE
                   |
                   v
              ORDERS/FILLS
```

---

# 3. Timestamp Model

Every externally sourced event should preserve:

```text
event_time
receive_time
process_time
```

Definitions:

### event_time

Timestamp assigned by the originating source.

Examples:

* exchange timestamp
* publication timestamp
* official release timestamp

### receive_time

When SRA-Nexus first received the event.

### process_time

When normalization/classification made the event available for downstream use.

All internal timestamps should be timezone-aware UTC.

Historical replay must use only information whose realistic availability time is less than or equal to the simulated clock.

---

# 4. Instrument

```text
Instrument
{
    instrument_id
    ticker
    exchange
    asset_type
    currency
    sector
    industry
    country
    tick_size
    lot_size
}
```

Ticker strings are metadata, not primary identifiers.

---

# 5. Entity

An entity represents something news can refer to.

Examples:

* company
* government
* country
* person
* commodity
* industry
* sector
* central bank
* economic indicator
* geographic region

Conceptual structure:

```text
Entity
{
    entity_id
    entity_type
    canonical_name
    aliases[]
    metadata
}
```

---

# 6. RawNewsItem

Every received source item becomes:

```text
RawNewsItem
{
    news_id

    source
    source_type

    provider_item_id

    headline
    body
    url

    event_time
    receive_time
    process_time

    provider_tickers[]
    provider_entities[]

    language

    raw_metadata

    content_hash
}
```

The raw record is immutable.

Possible source types include:

```text
FINANCIAL_NEWS
WIRE
SEC
COMPANY_RELEASE
MACRO_CALENDAR
CENTRAL_BANK
GOVERNMENT
GLOBAL_NEWS
SOCIAL
```

---

# 7. CanonicalEvent

Many raw items may represent one real event.

Example:

```text
Reuters article
Bloomberg article
company press release
CNBC article
```

may all describe the same acquisition.

These should map to one canonical event where appropriate.

```text
CanonicalEvent
{
    event_id

    first_event_time
    first_receive_time
    last_update_time

    event_type
    event_subtype

    headline_summary
    event_summary

    source_news_ids[]

    entities[]
    instruments[]

    sectors[]
    industries[]
    countries[]
    commodities[]
    macro_factors[]

    sentiment
    surprise
    novelty
    severity
    relevance
    confidence
    credibility

    expected_duration

    event_state
}
```

Possible states:

```text
NEW
DEVELOPING
CONFIRMED
UPDATED
RESOLVED
RETRACTED
```

---

# 8. Event Taxonomy

Initial top-level categories:

```text
COMPANY
SECTOR
MACRO
GEOPOLITICAL
REGULATORY
MARKET_STRUCTURE
SYSTEMIC
COMMODITY
CURRENCY
RATE
```

Example subtypes:

```text
COMPANY.EARNINGS
COMPANY.GUIDANCE
COMPANY.MERGER
COMPANY.PRODUCT
COMPANY.LAWSUIT
COMPANY.MANAGEMENT
COMPANY.SEC_FILING

MACRO.CPI
MACRO.JOBS
MACRO.GDP
MACRO.RETAIL_SALES

RATE.FED_DECISION
RATE.FED_SPEECH

GEOPOLITICAL.WAR
GEOPOLITICAL.SANCTION
GEOPOLITICAL.TRADE_RESTRICTION

REGULATORY.ANTITRUST
REGULATORY.EXPORT_CONTROL
REGULATORY.DRUG_APPROVAL

SYSTEMIC.BANK_FAILURE
SYSTEMIC.EXCHANGE_OUTAGE
```

The taxonomy must remain extensible.

---

# 9. Event Exposure

An event may affect instruments directly or indirectly.

For instrument (i) and event (e):

[
X_{i,e}=D_{i,e}M_{i,e}
]

where:

[
D_{i,e}\in[-1,1]
]

is directional exposure and:

[
M_{i,e}\in[0,1]
]

is relationship magnitude.

Therefore:

[
X_{i,e}\in[-1,1]
]

Example:

```text
Taiwan semiconductor disruption
        |
        +-- TSM
        |
        +-- semiconductor supply
                |
                +-- NVDA
                +-- AMD
                +-- AAPL
```

Possible relationship types:

```text
DIRECT_COMPANY
COMPETITOR
CUSTOMER
SUPPLIER
SECTOR
INDUSTRY
COUNTRY
COMMODITY
MACRO
REGULATORY
```

---

# 10. Event Relevance

For instrument (i) and event (e):

[
Rel_{i,e}\in[0,1]
]

Relevance may depend on:

* direct mention
* ownership relationship
* supply-chain relationship
* competitor relationship
* sector relationship
* country exposure
* commodity exposure
* macro exposure

---

# 11. Sentiment

[
Sent_e\in[-1,1]
]

Interpretation:

```text
-1 = strongly negative
 0 = neutral
+1 = strongly positive
```

Sentiment alone is not a trade signal.

---

# 12. Surprise

For quantitative releases:

[
Surprise_e=Actual_e-Expected_e
]

A normalized form is:

[
ZSurprise_e=
\frac{Actual_e-Expected_e}
{\sigma_e}
]

where (\sigma_e) represents a historical forecast-error scale.

For qualitative events, surprise can eventually be approximated using:

[
Surprise_e\approx-\log P(Event_e|\text{prior information})
]

This is a research concept rather than an initial implementation requirement.

---

# 13. Novelty

Let semantic similarity between a new item and prior canonical events be:

[
sim(new,e)
]

Then an initial conceptual novelty measure is:

[
Novelty=
1-\max_e sim(new,e)
]

with:

[
Novelty\in[0,1]
]

A repeated article should have low novelty.

A genuinely new development should have higher novelty.

---

# 14. Credibility

[
Cred_e\in[0,1]
]

Credibility represents reliability of the underlying information.

Possible inputs:

* official regulatory filing
* government publication
* company release
* established wire
* established financial publication
* independent corroboration
* correction/retraction history

Credibility is not a political or ideological score.

---

# 15. Severity

[
Sev_e\in[0,1]
]

Severity represents expected economic significance.

Initial severity may be rule-based.

Later it should preferably be empirically calibrated using measures such as expected abnormal price movement conditional on event class.

---

# 16. Event Confidence

[
Conf_e\in[0,1]
]

Possible inputs:

* source credibility
* number of independent confirmations
* entity-linking confidence
* classification confidence
* extraction confidence

Confidence is not the same as directional conviction.

---

# 17. Event Decay

Initial model:

[
Decay_e(t)=e^{-(t-t_e)/\tau_e}
]

where (\tau_e) depends on event category.

Then event intensity for instrument (i) may be represented as:

[
EI_{i,e}(t)=
Rel_{i,e}
\cdot
Sev_e
\cdot
Novelty_e
\cdot
Cred_e
\cdot
Conf_e
\cdot
Decay_e(t)
]

Directional event intensity:

[
DEI_{i,e}(t)=EI_{i,e}(t)D_{i,e}
]

This formula is an initial research definition and may be recalibrated later.

---

# 18. NewsState

For instrument (i) at historical time (t):

```text
NewsState
{
    instrument_id
    as_of

    positive_event_intensity
    negative_event_intensity

    company_event_risk
    sector_event_risk
    macro_event_risk
    geopolitical_event_risk
    regulatory_event_risk
    systemic_event_risk

    news_volume
    news_acceleration

    novelty_intensity
    uncertainty
    confidence

    active_event_ids[]
    direct_event_exposures[]
    indirect_event_exposures[]
}
```

A required eventual interface:

```python
get_news_state(
    instrument_id: InstrumentId,
    as_of: datetime,
) -> NewsState
```

No event unavailable at `as_of` may affect this result.

---

# 19. News Uncertainty

Define:

[
U_i(t)\in[0,1]
]

Potential causes of high uncertainty:

* conflicting reports
* rapidly evolving events
* low-credibility initial reporting
* contradictory directional implications
* abnormally high news arrival rate
* unresolved major event

High uncertainty should normally increase risk requirements rather than directly determine trade direction.

---

# 20. BookEvent

Fundamental order-book event:

```text
BookEvent
{
    event_id
    instrument_id

    exchange_time
    receive_time
    process_time

    sequence_number

    action
    side

    price
    quantity

    order_id
    trade_id

    flags
}
```

Possible actions:

```text
ADD
MODIFY
CANCEL
EXECUTE
DELETE
RESET
```

---

# 21. BookSnapshot

```text
BookSnapshot
{
    instrument_id
    timestamp

    bid_levels[]
    ask_levels[]

    best_bid
    best_ask

    spread
    midprice
    microprice

    bid_depth_N
    ask_depth_N
}
```

Midprice:

[
M_t=\frac{BestBid_t+BestAsk_t}{2}
]

---

# 22. Order-Book Imbalance

For (K) levels:

[
OBI_K=
\frac{
\sum_{k=1}^{K}B_k-\sum_{k=1}^{K}A_k
}{
\sum_{k=1}^{K}B_k+\sum_{k=1}^{K}A_k
}
]

Range:

[
[-1,1]
]

This is a supporting feature, not the SRA trading rule.

---

# 23. Weighted Depth

[
WD_B=\sum_{k=1}^{K}w_kB_k
]

[
WD_A=\sum_{k=1}^{K}w_kA_k
]

with:

[
w_1>w_2>\dots>w_K
]

Possible starting weights:

[
w_k=e^{-\alpha(k-1)}
]

The value of (\alpha) must be calibrated rather than assumed optimal.

---

# 24. Aggressive Order Flow

Within window (W):

[
V_W^{buy}=\sum BuyInitiatedVolume
]

[
V_W^{sell}=\sum SellInitiatedVolume
]

Signed flow:

[
OF_W=V_W^{buy}-V_W^{sell}
]

---

# 25. Normalized Aggression

Sell aggression:

[
A_t^{sell}=
\frac{V_W^{sell}}
{WD_B(t_0)}
]

Buy aggression:

[
A_t^{buy}=
\frac{V_W^{buy}}
{WD_A(t_0)}
]

This measures aggressive flow relative to liquidity available to absorb it.

---

# 26. Liquidity Shock

A liquidity shock is an unusually strong aggressive event relative to current liquidity and market conditions.

Candidate features:

* normalized aggression
* aggressive-volume velocity
* price levels consumed
* immediate midprice displacement
* spread
* volatility regime

An initial research score may be:

[
S_t=
z(A_t)
+
\beta_1z(Velocity_t)
+
\beta_2z(LevelsConsumed_t)
+
\beta_3z(|\Delta M_t|)
]

A shock candidate exists when:

[
S_t>\theta_S
]

Initially prefer transparent thresholds and retain all raw components rather than immediately optimizing coefficients.

---

# 27. LiquidityShock Object

```text
LiquidityShock
{
    shock_id
    instrument_id

    direction

    start_time
    end_time

    start_event
    end_event

    aggressive_volume
    normalized_aggression

    levels_consumed

    pre_spread
    pre_depth

    immediate_price_change

    shock_score
}
```

Direction:

```text
BUY_SHOCK
SELL_SHOCK
```

---

# 28. Price Impact

For shock (k) at horizon (h):

[
PI_k(h)=M_{t_k+h}-M_{t_k^-}
]

Volume-normalized impact:

[
I_k(h)=
\frac{|PI_k(h)|}
{AggressiveVolume_k}
]

Directional impact:

[
DI_k(h)=Direction_k\cdot PI_k(h)
]

where:

```text
BUY_SHOCK  = +1
SELL_SHOCK = -1
```

Positive directional impact means price moved in the aggressor's direction.

---

# 29. Depth Depletion

On the attacked side:

[
D_0=Depth_{pre-shock}
]

[
D_{min}=MinimumDepthDuringShock
]

Then:

[
ConsumedDepth=D_0-D_{min}
]

---

# 30. Replenishment Ratio

At horizon (h):

[
Replenished(h)=Depth(t+h)-D_{min}
]

and:

[
RR(h)=
\frac{Replenished(h)}
{ConsumedDepth}
]

Interpretation:

```text
RR = 0    no recovery
RR = .5   half recovered
RR = 1    fully recovered
RR > 1    depth exceeds pre-shock level
```

Values greater than 1 should not be clamped.

---

# 31. Recovery Time

For recovery threshold (q):

[
\tau_q=
\min{h:RR(h)\ge q}
]

Track thresholds such as:

```text
25%
50%
75%
100%
```

Store both event-time and wall-clock recovery where available.

---

# 32. Resiliency Vector

Do not initially compress resiliency into one arbitrary score.

Use:

[
R_k=
[
RR_5,
RR_{10},
RR_{25},
RR_{50},
RR_{100},
\tau_{25},
\tau_{50},
\tau_{75},
\tau_{100}
]
]

Later:

[
ResScore=f(R_k)
]

may be learned.

---

# 33. Multi-Level Recovery

For level (j):

[
RR_j(h)=
\frac{
Depth_j(t+h)-Depth_{j,min}
}{
Depth_{j,pre}-Depth_{j,min}
}
]

Construct:

[
RC(h)=
[
RR_1(h),RR_2(h),...,RR_K(h)
]
]

Near-touch strength:

[
NTS=
\sum_{j=1}^{K}w_jRR_j
]

Deep-support ratio:

[
DSR=
\frac{
\sum_{j=2}^{K}w_jRR_j
}{
RR_1+\epsilon
}
]

The goal is to distinguish broad recovery from a single potentially ephemeral wall.

---

# 34. Net Liquidity Provision

For side (s), window (W):

[
NLP_s(W)=Adds_s(W)-Cancels_s(W)
]

Normalized form:

[
NNLP_s(W)=
\frac{
Adds_s-Cancels_s
}{
Adds_s+Cancels_s+\epsilon
}
]

This approximates a range of:

[
[-1,1]
]

---

# 35. Liquidity Credibility

Displayed liquidity should be weighted according to behavior.

Potential order-level inputs:

* lifetime
* executed fraction
* cancelled fraction
* replenishment count
* attacks survived

Candidate order credibility:

[
OC_o=
w_1ExecutedFraction+
w_2LifetimeScore+
w_3ReplenishmentScore+
w_4SurvivalScore-
w_5CancelFraction
]

normalized to:

[
OC_o\in[0,1]
]

Side-level credibility:

[
LC_s=
\frac{
\sum_o Quantity_oOC_o
}{
\sum_o Quantity_o
}
]

Credible depth:

[
CredibleDepth_s=Depth_sLC_s
]

The individual components should remain available rather than retaining only the composite score.

---

# 36. Flow Toxicity

Toxicity attempts to measure persistent directional/informed flow likely to overwhelm replenishing liquidity.

Do not initially compress it into one scalar.

Track a toxicity vector.

---

# 37. Flow Persistence

[
FP_t=
\frac{
|\sum OF_t|
}{
\sum |OF_t|+\epsilon
}
]

Near 0 means aggression alternates.

Near 1 means aggression is consistently directional.

---

# 38. Shock Persistence

For the last (n) shocks:

[
SP_t=
\frac{
|\sum Direction_k|
}{
n
}
]

Repeated same-direction shocks increase persistence.

---

# 39. Impact Escalation

For consecutive similarly directed shocks:

[
IE_k=
\frac{
I_k
}{
I_{k-1}+\epsilon
}
]

If:

[
IE>1
]

the aggressor is becoming more effective.

If:

[
IE<1
]

the aggressor is becoming less effective.

---

# 40. Replenishment Failure

At chosen horizon:

[
RF_k=1-RR_k
]

Increasing replenishment failure is potentially toxic.

---

# 41. News-Conditioned Toxicity

Let:

[
U_i(t)
]

be news uncertainty and:

[
EI_i(t)
]

be relevant event intensity.

An initial contextual feature is:

[
NT_i=U_iEI_i
]

The same order-book behavior may imply different risk during a quiet market than immediately after a high-impact unexpected event.

---

# 42. Toxicity Vector

Store:

[
T_t=
[
FP,
SP,
IE,
RF,
NewsIntensity,
NewsUncertainty,
SpreadExpansion,
VolatilityJump
]
]

A future model may estimate:

[
ToxicityScore=f(T_t)
]

---

# 43. Shock Pair

Two shocks may form a candidate pair when:

* same instrument
* same direction
* sufficiently close in event or clock time
* market regime remains comparable
* no structural break invalidates comparison

```text
ShockPair
{
    pair_id

    shock_1
    shock_2

    event_distance
    time_distance

    impact_delta
    replenishment_delta
    recovery_delta
    curvature_delta
    toxicity_delta
}
```

---

# 44. Aggressor Effectiveness

Define:

[
AE_k=
\frac{
DirectionalPriceImpact_k
}{
NormalizedAggression_k+\epsilon
}
]

For two similarly directed shocks:

[
\Delta AE=AE_2-AE_1
]

Core hypothesis:

[
\Delta AE<0
]

means the aggressor is becoming less effective.

For repeated sell shocks, declining aggressor effectiveness combined with strengthening bid resiliency may predict positive subsequent returns.

For repeated buy shocks, the symmetric condition may predict negative subsequent returns.

---

# 45. Absorption Efficiency

Candidate feature:

[
AbsEff_k=
\frac{
RR_k
}{
AE_k+\epsilon
}
]

Then:

[
\Delta AbsEff=
AbsEff_2-AbsEff_1
]

Increasing absorption efficiency means liquidity is recovering more effectively relative to the aggressor's ability to move price.

This is one of the primary SRA research features.

---

# 46. SRAState

```text
SRAState
{
    instrument_id
    timestamp

    shock_direction

    shock_1_features
    shock_2_features

    impact_delta
    aggressor_effectiveness_delta

    replenishment_delta
    recovery_time_delta

    near_touch_strength
    deep_support_ratio

    bid_nnlp
    ask_nnlp

    bid_credibility
    ask_credibility

    flow_persistence
    shock_persistence

    toxicity_vector

    spread
    volatility
    microprice_offset

    event_state_reference
}
```

`SRAState` does not contain a final order or position size.

---

# 47. Alpha Features

The alpha layer may use:

[
X_i(t)=
[
SRAState_i(t),
NewsState_i(t),
MarketRegime_t
]
]

Portfolio context should remain outside the raw expected-return estimator where possible.

---

# 48. Prediction Target

Do not initially train a BUY/SELL classifier.

Predict future return distributions.

For horizon (h):

[
Y_h=M_{t+h}-M_t
]

or:

[
R_h=
\frac{
M_{t+h}-M_t
}{
M_t
}
]

Potential horizons:

```text
10 events
25 events
50 events
100 events
250 events
```

---

# 49. AlphaOutput

```text
AlphaOutput
{
    instrument_id
    timestamp

    expected_return_10
    expected_return_25
    expected_return_50
    expected_return_100

    probability_up
    probability_down

    expected_adverse_excursion
    expected_favorable_excursion

    expected_holding_horizon

    confidence

    model_version
}
```

---

# 50. Trading Costs

Expected costs may include:

[
C_i=
SpreadCost+
Fees+
Slippage+
MarketImpact+
AdverseSelection
]

Then:

[
NetAlpha_i=
ExpectedReturn_i-C_i
]

No opportunity should advance merely because gross expected return is positive.

---

# 51. Market Regime

```text
MarketRegime
{
    timestamp

    volatility_state
    liquidity_state
    spread_state
    activity_state

    market_direction

    macro_event_state

    session_segment
}
```

Possible contextual features:

* volatility
* spread regime
* market-wide direction
* order-book activity
* time of day
* proximity to scheduled macro releases
* overall news intensity

---

# 52. Position

```text
Position
{
    instrument_id

    quantity
    average_price
    market_value

    unrealized_pnl
    realized_pnl

    entry_alpha
    current_alpha

    entry_time

    event_exposures[]
}
```

---

# 53. PortfolioState

```text
PortfolioState
{
    timestamp

    cash

    positions[]

    gross_exposure
    net_exposure

    sector_exposure[]
    industry_exposure[]
    country_exposure[]

    event_exposure[]

    covariance_matrix

    expected_volatility
    expected_cvar

    drawdown
}
```

---

# 54. Portfolio Optimization

For (n) candidate positions:

[
\mu=
[
\mu_1,\mu_2,...,\mu_n
]^T
]

where (\mu_i) represents estimated net return.

Let:

[
\Sigma
]

be the expected return covariance matrix.

Initial objective:

[
\max_w
\quad
\mu^Tw
------

## \lambda w^T\Sigma w

## \gamma TC(w)

\xi ER(w)
]

Potential later extension:

[
-\eta CVaR_\alpha(w)
]

---

# 55. Event Risk

Portfolio exposure to event (e):

[
PE_e=
\sum_i w_iX_{i,e}
]

Potential event-risk penalty:

[
ER(w)=
\sum_e
Severity_e|PE_e|
]

This allows current events to create risk relationships that may not yet exist in historical covariance estimates.

---

# 56. Capital Constraint

Long-only prototype:

[
\sum_iw_i\le C
]

and:

[
w_i\ge0
]

Cash is therefore a legitimate allocation.

The optimizer must not be forced to invest all available capital.

---

# 57. Per-Asset Constraint

[
w_i\le MaxPosition_i
]

`MaxPosition` may depend on:

* portfolio size
* estimated alpha
* confidence
* volatility
* executable liquidity
* event uncertainty
* portfolio concentration
* fractional-Kelly ceiling

---

# 58. Liquidity Constraint

[
w_i
\le
\kappa ExecutableLiquidity_i
]

The execution model determines `ExecutableLiquidity`.

---

# 59. Confidence-Adjusted Alpha

An initial conservative estimate may use:

[
\tilde{\mu_i}=\mu_iConfidence_i
]

Later the optimizer should ideally consume a full estimate distribution instead of a single confidence multiplier.

---

# 60. Fractional Kelly Ceiling

A possible position ceiling:

[
w_i\le f_KKelly_i
]

where:

[
0<f_K<1
]

Full Kelly should not be assumed appropriate because edge estimates are uncertain.

Kelly should serve as a ceiling or supplemental risk mechanism, not necessarily the primary optimizer.

---

# 61. ExecutionIntent

Portfolio output:

```text
ExecutionIntent
{
    instrument_id

    target_position
    current_position
    delta_position

    max_execution_cost

    alpha_expiry_time

    urgency

    reason_reference
}
```

This is still not an exchange order.

---

# 62. Execution Methods

Possible execution decisions:

```text
PASSIVE_JOIN
PASSIVE_IMPROVE
AGGRESSIVE_LIMIT
MARKETABLE_LIMIT
NO_TRADE
```

Expected capture:

[
ExpectedCapture=
ExpectedAlpha-
ExpectedExecutionCost
]

---

# 63. Passive Fill Probability

For passive order (o):

[
P(Fill_o|Q_o,V_{future},C_{future},T)
]

where:

* (Q) = queue position
* (V) = future opposing aggressive volume
* (C) = cancellations ahead
* (T) = available time

Expected passive value:

[
EV_{passive}=
P(fill)
\cdot ExpectedPostFillAlpha
---------------------------

ExpectedAdverseSelection
]

This can be compared with aggressive execution.

---

# 64. Risk Hierarchy

Hard risk overrides everything.

```text
RISK ENGINE
    >
EXECUTION
    >
PORTFOLIO OPTIMIZER
    >
ALPHA
```

---

# 65. Potential Hard Kill Conditions

Examples:

* maximum daily loss exceeded
* maximum drawdown exceeded
* stale market data
* exchange sequence gap
* news-feed outage where news state is required
* broker/exchange disconnect
* abnormal execution latency
* missing acknowledgements
* impossible/unexpected position
* portfolio exposure breach
* model input materially outside training distribution

---

# 66. Soft Risk Controls

Examples:

* elevated volatility
* high news uncertainty
* high flow toxicity
* correlation concentration
* widening spreads
* reduced liquidity
* deteriorating model confidence

Soft controls reduce exposure rather than necessarily disabling the entire system.

---

# 67. Exit Logic

Primary exits should be thesis-based.

For a long SRA reversal setup, possible exit conditions include:

[
Resiliency\downarrow
]

or:

[
AggressorEffectiveness\uparrow
]

or:

[
BidCredibility\downarrow
]

or:

[
AskProvision\uparrow
]

or:

[
Toxicity\uparrow
]

or:

[
NetAlpha\le0
]

or a material change in relevant event state.

Emergency risk exits remain separate and always override thesis logic.

---

# 68. Storage Layers

## Raw

```text
raw_news
raw_market_events
raw_macro_events
raw_reference_data
```

## Normalized

```text
canonical_events
entities
instruments
event_exposures
normalized_book_events
```

## Derived Features

```text
news_states
book_snapshots
liquidity_shocks
shock_responses
shock_pairs
sra_states
market_regimes
```

## Models

```text
alpha_predictions
risk_predictions
optimization_results
execution_predictions
```

## Trading

```text
orders
fills
positions
portfolio_snapshots
risk_events
```

---

# 69. Python Module Layout

```text
src/sra_nexus/

    common/
        types.py
        enums.py
        clock.py

    reference/
        instruments.py
        entities.py

    aggregator/
        sources/
        ingestion.py
        normalization.py
        deduplication.py
        classification.py
        entity_linking.py
        sentiment.py
        surprise.py
        event_graph.py
        decay.py
        state.py

    market_data/
        ingestion.py
        book.py
        snapshots.py

    sra/
        shock.py
        impact.py
        resiliency.py
        curvature.py
        liquidity_flow.py
        credibility.py
        toxicity.py
        shock_pair.py
        state.py

    regimes/
        market_regime.py

    alpha/
        features.py
        targets.py
        models.py
        calibration.py

    portfolio/
        covariance.py
        event_risk.py
        cvar.py
        kelly.py
        optimizer.py

    execution/
        queue.py
        fills.py
        costs.py
        router.py

    risk/
        limits.py
        health.py
        kill_switch.py

    backtest/
        replay.py
        clock.py
        evaluation.py

    storage/
        raw.py
        normalized.py
        feature_store.py

    monitoring/
        metrics.py
        logging.py
```

This is a target structure, not a requirement to create every module immediately.

---

# 70. Initial Research Hypotheses

## H1 — Resiliency

Liquidity recovery after normalized shocks contains information about subsequent price movement.

## H2 — Changing Aggressor Effectiveness

For repeated same-direction shocks:

[
\Delta AE<0
]

contains reversal information.

## H3 — Strengthening Resiliency

[
\Delta RR>0
]

improves H2.

## H4 — Faster Recovery

[
\Delta\tau<0
]

improves H2.

## H5 — Broad Recovery

Multi-level replenishment predicts better than best-level recovery alone.

## H6 — Opposite-Side Withdrawal

Liquidity withdrawal from the aggressor's side improves reversal prediction.

## H7 — Liquidity Credibility

Executed/replenished liquidity predicts more reliably than raw displayed liquidity.

## H8 — Toxicity

Persistent directional flow and increasing impact weaken or reverse the failed-aggression hypothesis.

## H9 — News Context

SRA outcomes differ meaningfully across:

* quiet information regimes
* high-event-risk regimes
* high-uncertainty regimes
* confirmed directional-news regimes

## H10 — Event-Aware Portfolio Risk

Current event exposures improve portfolio risk control beyond historical covariance alone.

---

# 71. Implementation Order

## Phase 1 — Aggregator Data Contracts

Implement:

* `Instrument`
* `Entity`
* `RawNewsItem`
* `CanonicalEvent`
* `EventExposure`
* `NewsState`

No API calls yet.

## Phase 2 — Deterministic Raw Ingestion

Use fixture JSON and a mock news provider.

Implement:

```text
fixture
 -> source adapter
 -> RawNewsItem
 -> raw store
```

## Phase 3 — Storage and Historical Queries

Use a replaceable repository abstraction.

SQLite is acceptable for initial development.

Critical property:

```python
get_news_state(instrument_id, as_of)
```

must never return future information.

## Phase 4 — First Real Data Provider

Integrate one source only.

Do not integrate multiple APIs simultaneously.

## Phase 5 — Canonical Events

Implement deduplication and event clustering.

Start deterministic/simple.

## Phase 6 — Entity and Instrument Mapping

Build direct relationships first.

Add indirect event propagation later.

## Phase 7 — NewsState

Aggregate active canonical events into a reproducible instrument-level information state.

## Phase 8 — Market-Data Layer

Implement book reconstruction and snapshots.

## Phase 9 — Shock / Resiliency Research

Implement SRA primitives and test hypotheses statistically.

## Phase 10 — Alpha Modeling

Only after the microstructure hypothesis demonstrates predictive value.

## Phase 11 — Portfolio Optimization

Only after meaningful expected-return estimates exist.

## Phase 12 — Execution Simulation

Add queue, fills, latency, adverse selection, and costs.

## Phase 13 — Integrated Historical Replay

Replay news, market events, positions, and portfolio state chronologically.

## Phase 14 — Paper Trading

Live data, simulated positions only.

## Phase 15 — Limited Live Adapter

Only after research, execution validation, risk systems, and paper trading are satisfactory.

---

# 72. First Concrete Milestone

The first code milestone is intentionally narrow:

```text
RawNewsItem
    ->
storage
    ->
CanonicalEvent
    ->
EventExposure
    ->
NewsState
```

Using deterministic fixture data.

Success means we can execute:

```python
state = get_news_state(
    instrument_id=some_instrument,
    as_of=some_timestamp,
)
```

and obtain a deterministic, tested result containing only information that could have been known at that timestamp.

No SRA logic is required for this first milestone.

---

# 73. Implementation Status

Repository scaffold: COMPLETE

Domain models: NOT STARTED

Aggregator raw ingestion: NOT STARTED

Aggregator storage: NOT STARTED

Canonical event generation: NOT STARTED

Entity linking: NOT STARTED

Event exposure graph: NOT STARTED

NewsState: NOT STARTED

Market-data ingestion: NOT STARTED

Order-book reconstruction: NOT STARTED

Shock detection: NOT STARTED

Resiliency analysis: NOT STARTED

Liquidity credibility: NOT STARTED

Toxicity analysis: NOT STARTED

Shock-pair analysis: NOT STARTED

Alpha model: NOT STARTED

Portfolio optimizer: NOT STARTED

Execution simulator: NOT STARTED

Risk engine: NOT STARTED

Historical replay: NOT STARTED

Paper trading: NOT STARTED
