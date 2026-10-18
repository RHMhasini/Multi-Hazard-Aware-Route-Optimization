# Component 4: mission contracts and safety validation

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
synthetic-record oracle, not real verification. Travel-time and distance metrics
remain caller assertions; this milestone adds no independent checks for them.

The response reports eligible IDs in input order; it does **not** select or rank
a route, establish graph-wide infeasibility, or dispatch a mission. Statuses are
the validation-only subset proposed for this milestone, not an agreed upstream
integration standard. Python 3.10+ syntax is used. No JSON parser, API, LLM,
road graph, coordinate snapping, closure feed, deadline calculation, detour
baseline, or upstream provenance adapter is included. Snapshot/source references
are identifiers, not verified hashes or guarantees of data integrity. Freshness
alone does not establish scientific validity or calibration. The versioned
contracts remain a draft; newly added evidence requirements deliberately make
old unbound evidence ineligible for a pass.

Run the synthetic unit tests from the repository root:

```text
py -3.13 -B -m unittest discover -s tests/component4 -v
```

This research prototype is not suitable for real emergency operations.
