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

## Milestone D Reference Data and Economic Relationships

`EntityRepository` and `InstrumentRepository` are provider-independent
reference-data boundaries. The initial deterministic implementations support:

* immutable insert and identifier lookup for `Entity` and `Instrument`;
* normalized exact canonical-name and alias lookup;
* explicit ambiguity when multiple entities share a canonical name or alias;
* ticker lookup constrained by optional exchange and asset type;
* explicit ambiguity when ticker metadata identifies multiple instruments; and
* validity-aware entity-to-instrument association queries.

Alias and canonical-name lookup uses Unicode-compatible, case-insensitive exact
normalization. It never resolves a collision by insertion order. Ticker lookup
normalizes case but treats ticker as metadata rather than identity.

The entity-to-instrument relationship is explicit:

```text
EntityInstrumentLink
{
    link_id
    entity_id
    instrument_id
    relationship_type
    confidence
    valid_from
    valid_to
}
```

Initial relationship types are:

```text
PRIMARY_EQUITY
SECONDARY_EQUITY
ETF
ADR
OTHER
```

`confidence` is a dimensionless engineering value in `[0, 1]`. Optional
validity uses a half-open interval, `[valid_from, valid_to)`. Missing bounds mean
the bound is unknown; the system must not invent a date.

Structural economic relationships are separate from event direction:

```text
EntityRelationship
{
    relationship_id
    source_entity_id
    target_entity_id
    relation_type
    direction
    magnitude
    confidence
    valid_from
    valid_to
}
```

`direction` is `DIRECTED` or explicitly `SYMMETRIC`; it describes graph
orientation and never encodes a positive or negative event conclusion.
`COMPETITOR` is explicitly symmetric in the initial contract. `magnitude` and
`confidence` are dimensionless values in `[0, 1]`. Relationship validity also
uses `[valid_from, valid_to)`, and every historical graph query filters at the
canonical revision's aware UTC `available_at`.

The initial relationship taxonomy is deliberately small:

```text
OWNS_OR_ISSUES
COMPETITOR
CUSTOMER_OF
SUPPLIER_TO
MEMBER_OF_SECTOR
MEMBER_OF_INDUSTRY
LOCATED_IN
OPERATES_IN
EXPOSED_TO_COMMODITY
EXPOSED_TO_CURRENCY
REGULATED_BY
MACRO_SENSITIVE_TO
OTHER
```

SQLite development storage uses `entities`, `entity_aliases`, `instruments`,
`entity_instrument_links`, and `entity_relationships`. Name, alias, ticker,
validity, source-edge, and target-edge queries have explicit indexes. Domain
models and services do not contain SQL.

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
SPECULATIVE
OTHER
```

## Speculative Source Semantics

`SPECULATIVE` represents alternative or nontraditional information sources that
may contain useful market information but must not receive the same evidentiary
weight as official filings, government releases, or established financial news
sources.

An intended future `SPECULATIVE` source is politician securities transaction
data, including data obtained from [StockNest](https://stocknest.app/). StockNest
integration and politician-specific domain models are outside this milestone.

Future politician transaction records must distinguish these UTC timestamps
where available:

* `transaction_date`: when the reported securities transaction occurred;
* `disclosure_date`: when the transaction was officially disclosed;
* `source_publish_time`: when the source or provider published or exposed the
  record;
* `receive_time`: when SRA-Nexus received the information; and
* `process_time`: when SRA-Nexus made the information usable.

The transaction date is event metadata, not the information-availability time.
Historical replay must never expose a politician transaction before it was
publicly disclosed and realistically available through the source, receipt, and
processing timeline.

## Milestone B Raw Ingestion and Storage

Raw ingestion uses a provider-independent `NewsSource` protocol. A source returns
a `NewsSourceBatch` containing validated `RawNewsItem` values plus structured
per-record validation failures. Provider-shaped data ends at this boundary. The
initial `MockNewsSource` reads only local JSON fixtures and maps fixture keys such
as `provider`, `provider_record_id`, `title`, `content`, and `published_at` into
the existing raw contract. It performs no network access or canonicalization.

Raw content identity uses SHA-256 policy
`sra-nexus.raw-news-content.v1`. The deterministic JSON hash input contains:

* the policy identifier;
* source;
* headline;
* body;
* URL; and
* source event/publication time.

Text inputs are Unicode NFC-normalized, CRLF and CR line endings become LF, and
outer whitespace is removed. Optional values remain JSON `null`. The event time
is normalized to UTC and serialized with microsecond precision. Internal IDs,
provider item IDs, source type, provider ticker/entity annotations, language,
raw metadata, `receive_time`, and `process_time` are excluded. Consequently,
receipt or processing latency cannot give identical source content a new
identity; provider IDs have a separate duplicate rule.

Raw insertion is immutable and applies duplicate rules in this order:

1. an existing non-null `(source, provider_item_id)` pair is a provider-item
   duplicate;
2. an existing content hash is a content duplicate; and
3. an existing `news_id` is an internal-ID collision.

No rule overwrites the first stored record. Similar reports from different
providers remain separate raw records when their deterministic content differs;
semantic event clustering is deferred.

`RawNewsRepository` is the storage boundary. Its initial implementation uses the
standard-library `sqlite3` module for local development only. The `raw_news`
table stores every `RawNewsItem` field. `news_id` is the primary key,
`content_hash` is unique, and a partial unique index covers
`(source, provider_item_id)` only when the provider ID is non-null. A
`(process_time, news_id)` index supports deterministic historical queries.
Collections and metadata use deterministic JSON; timestamps use UTC ISO 8601
with microsecond precision. Schema initialization is explicit.

Historical raw availability is defined exclusively by `process_time <= as_of`.
The cutoff must be timezone-aware and is normalized to UTC. Results sort by
`process_time`, then `news_id`. An earlier `event_time` or `receive_time` never
makes a record visible before processing, preventing look-ahead leakage.

`RawNewsIngestionService` processes records independently. Source validation
failures are returned as structured failure details while valid new and duplicate
records in the same batch continue. Unexpected programming, fixture-level, and
database/infrastructure errors propagate. The service depends only on
`NewsSource` and `RawNewsRepository`; it contains no interpretation or trading
logic. See [ADR 0001](decisions/0001-raw-news-sqlite-and-availability.md) for the
storage and historical-availability decision.

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

## Milestone C Deterministic Canonicalization

A canonical event has one stable `CanonicalEventId` and an append-only sequence
of immutable `CanonicalEventRevision` snapshots. Each revision contains the
complete materialized `CanonicalEvent`, its revision number, clustering metadata,
and an explicit `available_at`. For deterministic offline processing,
`available_at` equals the triggering `RawNewsItem.process_time`. Historical
queries select only the highest revision satisfying `available_at <= as_of`.
Earlier revisions are never overwritten.

The deterministic classifier uses one ordered central rule table over normalized
headline and body text. It returns top-level type, namespaced subtype, an initial
engineering confidence, matched rule identifiers, and an explanation. Rules
cover the small taxonomy below. When no phrase rule matches, source-type
fallbacks select a conservative `OTHER` subtype; ordinary unmatched sources use
`COMPANY.OTHER`. `SPECULATIVE` is only a source category and receives no special
event type or clustering path.

Comparison text is Unicode NFKC-normalized, case-folded, punctuation-normalized
to spaces, and whitespace-collapsed. Token sets remove a small explicit stopword
set and apply only a small declared alias table for common event wording. Raw
headline and body text remain unchanged. Event anchors prefer provider tickers.
When tickers are absent, provider entity strings and obvious uppercase tokens
provide lightweight local anchors; this does not create entities or perform
symbol lookup.

Candidate retrieval uses the latest revision historically available at the
incoming item timestamp and is indexed by event type, subtype, availability
window, and anchor. The maximum candidate age is 36 hours. Similarity is:

```text
score = 0.55 * headline_jaccard
      + 0.25 * anchor_jaccard
      + 0.10 * temporal_proximity
      + 0.10 * exact_type_and_subtype
```

Headline and anchor Jaccard are intersection over union; two empty sets provide
zero evidence. Temporal proximity decays linearly from 1 at zero age to 0 at 36
hours. These are initial engineering values, not empirically calibrated trading
parameters. The clustering threshold is 0.55.

Hard guards reject candidates with an incompatible event type, incompatible
subtype, age beyond 36 hours, disjoint non-empty ticker anchors, or—when the
event type is `COMPANY`—no shared anchor. Different providers incur no penalty.
If the two highest qualifying scores differ by less than the 0.05 ambiguity
margin, canonicalization returns `AMBIGUOUS` and persists no decision.

The first revision is `NEW`. A matching independent provider advances an event
to `DEVELOPING`. A matching independent official source confirms only compatible
classes: company releases for company events; SEC sources for company or
regulatory events; government sources for geopolitical, macro, regulatory, or
systemic events; and central banks for macro or rate events. A matching new
NewsId from the same source becomes `UPDATED`. `CONFIRMED` remains confirmed.
Every matched new NewsId creates a revision so provenance changes remain
historically visible, even when coverage is repetitive.

SQLite development storage separates stable event identity, immutable revisions,
historical revision-source membership, revision anchors, and globally unique raw
NewsId assignment. Revision numbers and availability are monotonic. Processing a
previously assigned NewsId returns `ALREADY_PROCESSED`; it cannot create another
event or revision. See
[ADR 0002](decisions/0002-immutable-canonical-event-revisions.md).

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

Initial namespaced subtypes:

```text
COMPANY.EARNINGS
COMPANY.GUIDANCE
COMPANY.MERGER_ACQUISITION
COMPANY.PRODUCT
COMPANY.MANAGEMENT
COMPANY.LEGAL
COMPANY.SEC_FILING
COMPANY.CAPITAL_RAISE
COMPANY.BUYBACK
COMPANY.DIVIDEND
COMPANY.OTHER

MACRO.CPI
MACRO.JOBS
MACRO.GDP
MACRO.RETAIL_SALES
MACRO.OTHER

RATE.CENTRAL_BANK_DECISION
RATE.CENTRAL_BANK_SPEECH
RATE.OTHER

GEOPOLITICAL.CONFLICT
GEOPOLITICAL.SANCTION
GEOPOLITICAL.TRADE_RESTRICTION
GEOPOLITICAL.ELECTION
GEOPOLITICAL.OTHER

REGULATORY.ANTITRUST
REGULATORY.EXPORT_CONTROL
REGULATORY.APPROVAL
REGULATORY.ENFORCEMENT
REGULATORY.OTHER

SYSTEMIC.BANK_FAILURE
SYSTEMIC.EXCHANGE_OUTAGE
SYSTEMIC.MARKET_DISRUPTION
SYSTEMIC.OTHER
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

## Milestone D Entity Linking and Exposure Graph

Milestone D processes one immutable `CanonicalEventRevision` at a time:

```text
CanonicalEventRevision
    -> deterministic entity linking
    -> explicit instrument resolution
    -> bounded economic-relationship traversal
    -> revision-specific EventExposure records and ExposurePaths
```

The result is contextual economic exposure. It is never a `BUY`, `SELL`,
position size, or order.

### Deterministic Entity Linking

The initial linker uses only local, explicit evidence in this precedence:

1. `RawNewsItem.provider_tickers`, resolved through `InstrumentRepository` and
   a valid `EntityInstrumentLink`;
2. `RawNewsItem.provider_entities`, resolved by exact canonical name or alias;
3. explicit sector, industry, country, commodity, and macro context already on
   the canonical revision; and
4. exact canonical-name or alias phrases in the canonical headline and summary;
   and
5. fallback ticker-anchor tokens retained by deterministic canonicalization.

It uses no LLM, embedding, external symbol lookup, or statistical NLP. A match
produces an auditable `EntityLinkResult` containing `entity_id`, matched text,
typed method, confidence, primary-subject flag, and explanation. Unknown
provider metadata remains an explicit unresolved result. A collision produces a
typed entity or ticker ambiguity; the linker never silently chooses one.

Initial match confidences are centralized engineering priors:

```text
provider ticker:  1.00
provider entity:  0.95
canonical name:   0.90
alias:            0.85
exact context:    0.80
```

These values represent deterministic mapping confidence, not source
credibility, sentiment, or empirical return estimates.

Resolved entities become immutable revision-specific records:

```text
EventEntityLink
{
    event_id
    revision_id
    revision_number
    entity_id
    role
    relevance
    confidence
    is_direct
    matched_text
    match_method
    explanation
    available_at
}
```

Roles are limited to primary subject, secondary subject, counterparty, country,
sector, industry, commodity, regulator, macro, and other context.

### Direct and Indirect Exposure

An instrument connected to a directly linked event entity through a valid
`EntityInstrumentLink` receives a direct exposure. Initial direct issuer
magnitude is `1.0`; this is an engineering prior, not a calibrated expected
return. An instrument reached after one or more structural entity relationships
receives an indirect exposure. Direct and indirect evidence for the same
instrument remain separately materialized.

Unknown event direction is represented as `0`, meaning no deterministic
directional conclusion has been established. The initial explicit semantic
rules cover buyback authorization, dividend suspension/cancellation, regulatory
approval/rejection, and sanctions. All other cases remain `0`. This is not a
sentiment model.

### Bounded Propagation and Formulas

Traversal uses path-local visited entities and defaults to:

```text
max_depth = 2
decay_factor = 0.75
relevance_decay = 0.70
```

For a path at depth (d):

[
Magnitude_{path}=
ParentMagnitude
\cdot
\prod_{j=1}^{d}RelationshipMagnitude_j
\cdot
DecayFactor^d
]

[
Confidence_{path}=
EntityLinkConfidence
\cdot
\prod_{j=1}^{d}RelationshipConfidence_j
\cdot
EntityInstrumentLinkConfidence
]

[
Relevance_{path}=
ParentRelevance
\cdot
RelevanceDecay^d
]

All values are dimensionless and remain in `[0, 1]`. The parameters are
centralized deterministic engineering values and have not been optimized on
fixture outcomes.

Traversal orientation is also centralized. Supplier, customer, and ownership
edges traverse forward. Membership, location, operation, commodity, currency,
regulation, and macro-sensitivity edges traverse from the referenced context
back toward exposed entities. Explicitly symmetric edges traverse both ways.

Sign propagation is deliberately more conservative than graph traversal:

* ownership preserves a known direction;
* sector and industry membership preserve direction only for sector events;
* location and operation preserve direction only for geopolitical events;
* regulation preserves direction only for regulatory events; and
* supplier, customer, competitor, commodity, currency, macro, and unclassified
  relationships default to unknown direction.

A relationship policy may explicitly preserve, reverse, or erase direction.
Connectivity and magnitude remain available even when direction becomes zero.

### Exposure Paths and Multi-Path Materialization

Every direct and indirect path is retained:

```text
ExposurePath
{
    path_id
    event_id
    revision_id
    revision_number
    available_at
    starting_entity_id
    relationship_ids[]
    entity_ids[]
    target_entity_id
    target_instrument_id
    depth
    direction
    magnitude
    relevance
    confidence
}
```

Path IDs are deterministic UUIDv5 values derived from immutable revision and
path identity. Cycles are invalid in a stored path.

For each `(event_id, revision, instrument_id, is_direct)` key, paths produce one
materialized exposure. Multiple path magnitudes and confidences use the bounded
combination:

[
CombinedValue=1-\prod_p(1-Value_p)
]

Relevance is the maximum path relevance. The relation type comes from the
strongest path, with deterministic confidence and path-identity tie breaks.
Unknown-direction paths do not conflict with a known sign. If positive and
negative deterministic paths both exist, materialized direction becomes `0`
and `direction_conflict` is `True`; processing order never selects the winner.
All path evidence remains separately queryable.

### Historical Persistence and Idempotency

`EventExposureService` depends on repository protocols and contains no SQL. It
loads one exact canonical revision, links entities, resolves instruments,
traverses validity-filtered relationships, materializes exposures, and saves the
complete result.

The SQLite development backend uses:

```text
event_entity_link_runs
event_entity_links
event_exposure_runs
event_exposures
exposure_paths
```

Records are immutable by canonical `revision_id`. Run markers preserve the
difference between an unprocessed revision and a processed revision with zero
links or exposures. Reprocessing an existing revision returns
`ALREADY_PROCESSED` with the same links, exposures, and paths. A conflicting
rewrite is rejected.

Initial deterministic offline processing assigns the exposure snapshot the
canonical revision's `available_at`. Therefore a revision cannot expose an
entity or relationship before that revision is available. Historical event
queries select only the latest processed exposure revision satisfying
`available_at <= as_of`; instrument queries select one latest visible revision
per event. Any future asynchronous implementation must record a later realistic
availability time when graph processing is not synchronous.

`SPECULATIVE` remains solely a `NewsSourceType`. A speculative-source canonical
event follows the same entity and exposure pipeline and does not create a
`SPECULATIVE` event type or special graph logic. Source credibility remains a
later scoring concern.

See [ADR 0003](decisions/0003-revision-aware-event-exposure-graph.md).

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

The core research hypothesis remains Shock-Resiliency Asymmetry. It is based on:

* repeated same-direction liquidity shocks
* normalized aggression
* directional price impact
* aggressor effectiveness and changes in aggressor effectiveness
* replenishment ratio
* recovery time
* multi-level resiliency
* net liquidity provision
* liquidity credibility
* flow toxicity
* absorption efficiency
* news and event context
* market-regime context

`NewsState` is contextual information used alongside `SRAState` and
`MarketRegime`. It must not independently define `BUY` or `SELL` orders, and it
does not replace SRA with a news-driven trading strategy. The alpha layer
estimates return distributions from the combined feature context; later
allocation and execution layers retain their separate responsibilities.

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

Execution-cost components are non-negative cost magnitudes. For one modeled
execution leg, expected execution cost is approximately:

[
ExpectedExecutionCost_i=
SpreadCost_i+
BrokerCommission_i+
ExchangeFees_i+
RegulatoryFees_i+
TransactionTaxes_i+
Slippage_i+
MarketImpact_i+
AdverseSelection_i
]

All components in an estimate must use explicitly stated, compatible units. For
example, components may be monetary amounts in a stated reporting currency or
returns normalized to the same notional, but monetary amounts and return units
must not be mixed in one calculation.

When expected gross return and expected execution cost have been expressed in
the same units:

[
PreTaxNetAlpha_i=
ExpectedGrossReturn_i-ExpectedExecutionCost_i
]

No candidate trade should advance merely because expected gross return is
positive. Expected gross return must exceed expected execution costs by an
appropriate future safety margin before the candidate is considered
economically viable:

[
ExpectedGrossReturn_i>
ExpectedExecutionCost_i+RequiredSafetyMargin_i
]

Equivalently:

[
PreTaxNetAlpha_i>RequiredSafetyMargin_i
]

The safety margin must eventually reflect estimation uncertainty and must not be
silently treated as zero.

## Transaction Taxes and Investor Taxes

`TransactionTaxes` are taxes or levies mechanically caused by executing a
transaction. They may be included in the execution-cost model when applicable
to the instrument, venue, jurisdiction, side, and simulated time.

Ordinary investor income tax and capital-gains tax must not be modeled as fixed
per-trade execution costs. A future, separately configurable tax and accounting
layer may estimate such liabilities because they can depend on:

* jurisdiction
* account type
* realized gains and losses
* holding period
* tax lots
* loss carryforwards
* wash-sale or equivalent rules
* user-specific tax treatment

The core SRA strategy, alpha estimator, and portfolio optimizer must remain
usable without assuming any particular user's tax jurisdiction.

## Time-Aware and Reproducible Cost Assumptions

Execution-cost assumptions must be time-aware, versioned, and historically
reproducible. Where historical schedules are available, a backtest must use the
broker commission, exchange fee, regulatory fee, and transaction-tax schedules
applicable at the simulated timestamp. It must not expose a later schedule to an
earlier simulation time.

Future brokerage adapters must expose their fee schedules through configuration
or an execution-cost abstraction. One broker's current pricing must never be
hard-coded into SRA mathematics, alpha estimation, or portfolio optimization.
Backtest output must retain the cost-model and schedule versions needed to
reproduce an estimate.

## Round-Trip Economics

When a candidate trade implies both an entry and an exit, its economic test must
include both legs:

[
ExpectedRoundTripCost=
ExpectedEntryCost+ExpectedExitCost
]

Entry and exit estimates may use different timestamps, prices, venues,
liquidity states, order types, and execution methods. A short-horizon strategy
must not compare expected return with entry cost alone. In that case,
`ExpectedExecutionCost_i` in the pre-tax net-alpha equation represents the
expected round-trip cost.

## Future CostModel Contract

A future `CostModel` abstraction will produce a `CostEstimate` without becoming
part of SRA signal generation:

```text
CostEstimate
{
    spread_cost
    broker_commission
    exchange_fees
    regulatory_fees
    transaction_taxes
    slippage
    market_impact
    adverse_selection
    total_cost
}
```

`total_cost` is the sum of the component cost magnitudes. A `CostModel` should
eventually consume relevant information such as:

* instrument
* venue
* side
* quantity
* price
* order type
* liquidity state
* expected execution method
* timestamp

The exact estimate must also identify its units and the versioned assumptions or
fee schedules used.

A future `TaxModel` or accounting abstraction must remain separate from
`CostModel` and from SRA signal generation. When enabled, it may use realized
portfolio and tax-lot history plus explicit jurisdiction, account, and user
configuration to estimate income or capital-gains tax. It must be optional and
must not change the jurisdiction-neutral definition of the core strategy.

## Gross, Cost, and Net Performance Attribution

Future backtest reporting must retain at least these values separately:

```text
GrossPnL
SpreadCost
BrokerCommissions
ExchangeFees
RegulatoryFees
TransactionTaxes
Slippage
MarketImpact
AdverseSelection
TotalExecutionCost
PreTaxNetPnL
```

The attribution must preserve:

[
PreTaxNetPnL=GrossPnL-TotalExecutionCost
]

If the optional tax and accounting layer is enabled, reporting must additionally
retain:

```text
EstimatedIncomeTax
AfterTaxPnL
```

Only reporting final net P&L is insufficient; gross performance and every cost
component must remain available for attribution and reproducibility.

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
        repositories.py
        instruments.py
        entities.py

    aggregator/
        sources/
        ingestion.py
        normalization.py
        deduplication.py
        classification.py
        entity_linking.py
        entity_links.py
        exposures.py
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
        canonical.py
        event_graph.py
        normalized.py
        feature_store.py
        sqlite_reference.py
        sqlite_event_graph.py

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

Domain models: COMPLETE

Aggregator raw ingestion: COMPLETE

Aggregator storage: COMPLETE

Canonical event generation: COMPLETE

Entity linking: COMPLETE

Event exposure graph: COMPLETE

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
