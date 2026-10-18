# Component 4: mission validation and experimental ranking

This standalone, standard-library-only milestone provides frozen Python
dataclasses for `c4.mission-request/1.0.0` and
`c4.mission-decision/1.0.0`, plus `validate_candidates()`.
It does not import or modify Components 1–3.

Requests carry mission identity/type, vehicle type, urgency, explicit hard
limits, and expected graph/hazard snapshot IDs. Mission and vehicle labels are
caller-supplied, case-sensitive strings; there is no inferred mission policy.
Urgency never relaxes a constraint. Missing or mismatched route/snapshot IDs
prevent eligibility, including missing expected snapshot IDs in the request.

Hazard eligibility requires supported route-bound evidence, an explicit `KNOWN`
status, a finite score in
`[0, 1]`, complete route coverage, a timezone-aware observation time, age within
the caller's limit, and an unexpired validity interval if supplied. Missing,
invalid, unknown, stale, future-dated, or partially covered hazard evidence
produces an unknown check, never zero risk. Scores and limits are compared
without rounding. Age and numeric upper bounds are inclusive; expiry is
exclusive. `as_of` is a required argument, normalized to UTC when valid.
UTC conversion is guarded, including overflow/underflow and exceptions from
custom timezone implementations. Invalid candidate timestamps yield unknown
freshness without stopping other candidates. An invalid evaluation clock
sets `evaluated_at=None`, reports `evaluation_time_invalid`, and prevents
hazard eligibility instead of throwing. Naive timestamps are invalid.

Each assessment includes structured `freshness` and `coverage` results, with
status and reason codes. Freshness records normalized observation/expiry times
(null when unavailable or invalid), age in seconds, and the applied age limit.
Coverage distinguishes the reported complete-coverage flag from its supported
status. These results are retained even when another hard constraint fails.

Vehicle compatibility must be supplied in route/graph-bound `VehicleEvidence`
as a `frozenset` of permitted types. Missing compatibility is unknown; an empty
supported set means no vehicle type is compatible. The old candidate-level
`compatible_vehicle_types` field is retained but ignored for eligibility.
Optional travel-time/distance limits require finite, nonnegative
candidate metrics when enabled. Units are seconds, metres, and dimensionless
susceptibility. NoData sentinels, NaN, infinity, booleans, and numeric strings
are not accepted as known hazard scores.

All applicable checks are returned. Any failed check makes a candidate
ineligible; otherwise an unknown check prevents eligibility. Outcomes are:

- `eligible_candidates`: at least one candidate passes every required check.
- `insufficient_data`: none pass, but at least one cannot be resolved.
- `no_feasible_candidate`: every supplied candidate has a definite violation.
- `no_candidates`: the supplied candidate set is empty.

Invalid mission policy, unsupported request versions, and ambiguous duplicate
route IDs raise `ValueError`. Invalid candidate evidence and evaluation times
instead yield structured unknown checks. Inputs are not mutated.

## Evidence support and trust boundary

Hazard and vehicle evidence include `evidence_source` and
`verification_status`. A source reference, `VERIFIED` flag, `KNOWN` status, or
`complete_coverage=True` is **not proof** merely because a caller supplied it.
Validation also requires a separately injected `EvidenceVerifier`, after
checking identifiers/source/status. Without one, claims remain unknown. Hooks
must return the boolean `True`; exceptions or other results cannot grant
eligibility. Verification does not override invalid values or hard limits.

The verifier is a **trusted application dependency**, not something to accept
from an untrusted request. Future upstream adapters must independently check
source artifacts, snapshot identity, exact route binding, full-route coverage,
and vehicle restrictions. No such adapter or artifact verification is included
here. A dishonest or incorrectly wired verifier can still bless false claims;
this interface is not authentication or a security boundary. Tests use an exact
synthetic-record oracle, not real verification. Standalone hard validation still
treats travel-time and distance metrics as caller assertions; ranking adds a
separate metric-verification gate described below.

The validation response reports eligible IDs in input order; it does **not** select or rank
a route, establish graph-wide infeasibility, or dispatch a mission. Statuses are
the validation-only subset proposed for this milestone, not an agreed upstream
integration standard. Python 3.10+ syntax is used. No JSON parser, API, LLM,
road graph, coordinate snapping, closure feed, deadline calculation, detour
baseline, or upstream provenance adapter is included. Snapshot/source references
are identifiers, not verified hashes or guarantees of data integrity. Freshness
alone does not establish scientific validity or calibration. The versioned
contracts remain a draft; newly added evidence requirements deliberately make
old unbound evidence ineligible for a pass.

## Deterministic mission-aware ranking baseline

`ranking.py::rank_candidates(request, candidates, policy, as_of=..., verifier=...,
metric_verifier=...)`
first invokes the existing validator on the exact records being ranked. It does
not accept an externally supplied eligibility flag or an old validation result.
Only eligible routes enter the comparison set; rejected and unknown candidates
never affect normalization. The complete validation response is retained in the
ranking result, including request/mission IDs, snapshot IDs, and evaluation time.
No hard limit or evidence requirement is relaxed by ranking.

`RankingPolicy` is an explicit `c4.ranking-policy/1.0.0` contract with a policy
ID, numeric `major.minor.patch` policy version, exact mission-type label, and
time/exposure weights. The policy's mission type must match the request. There
is no automatic policy selection, urgency-based weighting, free-text inference,
or built-in operational policy. Policy owners must change the version when
changing its meaning or weights; no policy registry is included.

Weights must be finite, nonnegative, at most one, and sum to one within `1e-12`.
The implementation divides by their total to remove permitted floating-point
sum drift and returns the effective weights. At least one weight is positive.

Every eligible route must have finite, nonnegative `travel_time_sec` (seconds)
and `cumulative_hazard_exposure` (hazard-metres, conventionally sum of edge
length in metres times dimensionless susceptibility). Both metrics are required
even if one has zero weight. Missing values, negative sentinels, numeric strings,
booleans, NaN and infinities are not replaced with zero. Each candidate also needs
supported `RankingMetricEvidence`. Exposure is not a failure probability.

### Ranking metric evidence and trust boundary

`RankingMetricEvidence` binds both numeric values to the candidate's `route_id`,
the request's expected graph/hazard snapshot IDs, and an authoritative
`route_result_id`. It also records source reference, claimed verification status,
travel-time-model version, exposure-formula version, and explicit canonical
units: `s` and `hazard*m`. Model/formula versions must be nonempty identifiers
matching the authoritative result. No unit conversion or numeric tolerance is
applied: submitted evidence values must equal the values being ranked.

Neither these fields nor a caller-supplied source name, `VERIFIED` flag, or hash
is proof. Ranking requires a separately injected `RankingMetricVerifier` whose
`get_authoritative_metrics(route_result_id)` loads an independently held route
result or recomputes it from checked inputs. A boolean approval, digest or source
label is not an authoritative result. Ranking validates the returned record's
values, units, route identity, snapshots and model/formula versions, and compares
the entire record with the submitted evidence. Successful ranked entries retain
that matching evidence for inspection.

The verifier is trusted application wiring, **not request data**. An upstream
adapter must resolve result IDs through a trusted store/allowlist, check source
artifacts and version bindings, and return canonical results; it must not echo
candidate claims or merely compare self-reported hashes. Artifact validation,
independent computation, source authentication, calibration, and real upstream
integration are **not implemented by this interface**. A fabricated verifier can
still fabricate authority. Tests use separately held synthetic records solely to
exercise this contract, not as evidence of real-world verification.

Missing/malformed evidence, identity/value/unit/version mismatches, unsupported
records, a missing metric verifier, and verifier exceptions produce structured
metric issues and `insufficient_ranking_data` for the full eligible set. No route
is selected and no normalization bounds are computed. Noneligible routes are
never scored or required to provide ranking metric evidence. The existing hard
validator and duplicate-ID rejection are unchanged; metric verification is an
additional ranking gate, not a relaxation or replacement of those checks.

For the full eligible set C, normalize each minimization objective using:

```text
normalized_time     = (time - min_time(C)) / (max_time(C) - min_time(C))
normalized_exposure = (exposure - min_exposure(C)) / (max_exposure(C) - min_exposure(C))
score = effective_time_weight * normalized_time
      + effective_exposure_weight * normalized_exposure
```

An objective constant over C contributes normalized value zero because it cannot
distinguish candidates; this includes a singleton comparison set. That zero is
not an imputation for missing data. Lower scores rank first. Exact computed-score
ties break by case-sensitive Python string ordering of route ID, independent of
input order; no display rounding or tie tolerance is applied. A singleton can be
selected but has no evidence of superiority to unsupplied alternatives.

The `c4.mission-ranking/1.0.0` result contains the full policy, effective weights,
sorted comparison route IDs, normalization bounds, candidate ranks, raw metric
values, normalized values, weighted contributions, scores, selected ID, and
matching metric evidence, and structured metric issues. Outcomes are:

- `no_eligible_candidates`: validation produced no eligible routes; its nested
  response distinguishes empty input, definite violations, and unknown evidence.
- `insufficient_ranking_data`: at least one eligible candidate lacks a valid,
  supported ranking metric. Return issues with no ranking, bounds or selected route; do
  not silently compare a smaller subset.
- `selected`: every eligible route has both supported metrics; return the complete ranking
  and the first route's ID.

Scores depend on C and cannot be compared across different candidate sets or
evaluations. This is an experimental weighted scoring baseline, **not a validated
emergency-routing policy** or a globally optimal route search. Selection is
scoped to supplied eligible candidates and the evaluation instant; no live
re-routing, dispatch, metric provenance adapter, LLM, API or dashboard is added.

## Deterministic synthetic mission-run orchestrator

`orchestration.py::run_mission()` accepts the existing request/policy/provider,
evaluation-time hint, verifier dependencies and retry policy. It now also
**requires** a host-owned `ExecutionBudget` and `RunDependencies` identities.
Mission/policy compatibility uses the same `validate_mission_policy()` preflight
as ranking, before provider invocation or budget reservation. No production
provider or Component 3 API is assumed. The provider receives an `AlternativesCall`
with request, time hint, attempt and deterministic call ID, returning a typed
`c4.route-alternatives/1.0.0` envelope. JSON decoding/production transport is not
implemented; unsupported raw bytes/dictionaries fail as invalid responses.

The runner records `created -> acquiring -> evaluating -> finished` transitions.
Only `TransientProviderError` permits `retrying -> acquiring`, up to
`max_attempts` (default two, including the initial call). `ProviderError` is
permanent by default; unexpected exceptions and malformed/version-mismatched
responses are terminal provider failures. Response snapshotting/inspection and
error formatting are guarded. Only ordinary `Exception` is caught; process-level
termination such as `KeyboardInterrupt` and `SystemExit` propagates. There is no
retry after ranking, evidence mismatch, missing data, or duplicate-ID rejection.
Snapshots, constraints, weights and evidence never change to force recovery.

`rank_candidates()` is the only route-selection gate. Terminal run outcomes are
`selected`, `insufficient_data`, `no_eligible_candidates`, `provider_failure`,
`retry_exhausted`, `budget_exhausted`, `invalid_input`, and `evaluation_failure`. Underlying ranking
and validation outcomes remain in the trace: unresolved evidence is distinct
from an empty set or definite constraint violations. A valid candidate may still
be selected alongside rejected candidates, exactly as the existing gate permits.
Invalid run configuration raises `ValueError` before provider acquisition.

The returned frozen `MissionRunTrace` is now `c4.mission-run/2.0.0`, runner version
`synthetic-baseline/2.0.0`. It captures the request/policy, time hint, actual
evaluation time, mode, retry policy, budget checkpoints, ordered transitions,
tool calls, errors, and full ranking/validation result. `RunDependencies` records
versioned provider, verifier, metric-verifier and authoritative evidence-bundle
references, plus a clock identity for current mode. These labels are audit
metadata, not verification, authentication or proof that referenced bytes exist.

`snapshots.py` rebuilds allowlisted data records and normalizes dates to UTC.
It retains only immutable built-in scalar/tuple/frozenset data and known enums.
Mutable/unsupported values become frozen `InvalidValue` markers, never usable
measurement defaults; malformed structures fail closed. No caller-owned mutable
payload or custom timezone is retained in recorded inputs/results. This is data
isolation, not a sandbox against malicious in-process Python using reflection or
`object.__setattr__`. The trace is still in-memory, not durable or tamper-proof.

### Historical versus current evaluation

`EvaluationMode.HISTORICAL` is the default for existing synthetic use: evidence
is evaluated at `as_of`, independent of elapsed wall time. A historical selection
is **not** a claim of current safety.

`EvaluationMode.CURRENT` additionally requires a trusted host `EvaluationClock`
and its identity. The runner obtains UTC time **after acquisition**, rejects
invalid/regressing clock readings, and calls the existing ranking gate at that
time. Before releasing a selection it obtains a completion time and reuses the
existing freshness evaluator for the full eligible comparison set. Evidence
expiring during evaluation prevents selection; there is no implicit reranking
with a smaller set. The completion checks and clock failures are recorded.

Consumers must inspect the run's `outcome`, `mode` and `selected_route_id`.
The nested ranking is a recorded provisional evaluation: it can contain a chosen
candidate even when a later budget/freshness gate withholds the final selection.
A current result describes validity at its recorded completion check, never a
guarantee about subsequent execution or actual emergency safety.

### Shared host execution budget

`execution.py::HostExecutionBudget(scope_id, max_provider_calls, max_duration_sec)`
uses a monotonic clock and atomic reservation. The host must create one instance
for the entire controller session and retain it outside controller/request
control, passing it to every run. New run IDs do not reset counters or deadline.
Do not expose budget creation/replacement, verifier wiring or evaluation-mode
selection as AI-controlled actions. A host deliberately creating a new budget
can of course reset its own scope; this is not an authentication boundary.

The runner checks the budget before reserving provider calls, after successful
acquisition, before ranking, after ranking/verifier work and before releasing a
current selection. Explicit transient failures also check the shared budget
before retry. Exhaustion/clock failure is sticky; unavailable or inconsistent
budget responses fail closed. Local `max_attempts` remains an additional bound.

There is **no hard interruption** of a blocking provider, verifier or clock.
Callbacks may exceed the deadline; after control returns the runner withholds
selection and performs no further external work once exhaustion is detected.
External process isolation, cooperative transport timeouts/cancellation, durable
budget storage and cross-process enforcement remain future integration tasks.
No timeout thread or claim that synchronous Python can kill callbacks is added.

`replay_evaluation(trace, verifier=..., metric_verifier=...)` re-runs the existing
gate on recorded inputs at `EvaluationRecord.evaluated_at`, without acquiring
alternatives again. It returns a `ReplayResult` explicitly marked **historical**,
including the computed ranking and recorded dependency identities. Exact
reproduction requires retaining the same implementation and independently held
authoritative evidence bundle. Identity labels and recorded flags alone cannot
authorize selection. Changed/missing authority can change the replay result.
This replays the evaluation, not elapsed-time budget use or a current completion
gate. Unsupported trace/runner versions, including old version-1 traces, and
failed acquisition without an evaluation are rejected rather than guessed at.

`tests/component4/test_orchestration.py` contains the only synthetic provider and
authority implementations. Its fixed expected hazard, vehicle and metric records
are built separately from provider claims. These are labelled test fixtures,
not genuine trusted upstream records or evidence of scientific accuracy.

This is deterministic workflow orchestration, **not agentic AI behavior**: it
follows fixed transitions and retry rules. Future agentic experiments could
compare permitted tool-planning strategies against this baseline while retaining
the same deterministic gate. No LLM, LangGraph, multi-agent framework, dashboard
or API is introduced. There is no backoff, hard per-call interruption, cancellation
or live re-routing. Real upstream verification and an authoritative operational
clock source remain integration tasks; tests use synthetic clocks and records.

Run the synthetic unit tests from the repository root:

```text
py -3.13 -B -m unittest discover -s tests/component4 -v
```

This research prototype is not suitable for real emergency operations.
