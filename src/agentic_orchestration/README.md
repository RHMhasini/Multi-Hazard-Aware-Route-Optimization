# Component 4: mission contracts and safety validation

This standalone, standard-library-only milestone provides frozen Python
dataclasses for `c4.mission-request/1.0.0` and
`c4.mission-decision/1.0.0`, plus `validate_candidates()`.
It does not import or modify Components 1–3.

Requests carry mission identity/type, vehicle type, urgency, and explicit hard
limits. Mission and vehicle labels are caller-supplied, case-sensitive strings;
there is no inferred mission policy. Urgency never relaxes a constraint.

Hazard eligibility requires an explicit `KNOWN` status, a finite score in
`[0, 1]`, complete route coverage, a timezone-aware observation time, age within
the caller's limit, and an unexpired validity interval if supplied. Missing,
invalid, unknown, stale, future-dated, or partially covered hazard evidence
produces an unknown check, never zero risk. Scores and limits are compared
without rounding. Age and numeric upper bounds are inclusive; expiry is
exclusive. `as_of` is a required timezone-aware argument, normalized to UTC.

Vehicle compatibility must be explicitly supplied as a `frozenset` of permitted
types. Missing compatibility is unknown; an empty set means no vehicle type is
compatible. Optional travel-time/distance limits require finite, nonnegative
candidate metrics when enabled. Units are seconds, metres, and dimensionless
susceptibility. NoData sentinels, NaN, infinity, booleans, and numeric strings
are not accepted as known hazard scores.

All applicable checks are returned. Any failed check makes a candidate
ineligible; otherwise an unknown check prevents eligibility. Outcomes are:

- `eligible_candidates`: at least one candidate passes every required check.
- `insufficient_data`: none pass, but at least one cannot be resolved.
- `no_feasible_candidate`: every supplied candidate has a definite violation.
- `no_candidates`: the supplied candidate set is empty.

Invalid mission policy, unsupported request versions, ambiguous duplicate route
IDs, and invalid evaluation times raise `ValueError`. Invalid candidate evidence
instead yields structured unknown checks. Inputs are not mutated.

The response reports eligible IDs in input order; it does **not** select or rank
a route, establish graph-wide infeasibility, or dispatch a mission. Statuses are
the validation-only subset proposed for this milestone, not an agreed upstream
integration standard. Python 3.10+ syntax is used. No JSON parser, API, LLM,
road graph, coordinate snapping, closure feed, deadline calculation, detour
baseline, or upstream provenance adapter is included. Evidence coverage and
vehicle compatibility are caller assertions, not independently verified facts.
Freshness alone does not establish scientific validity or calibration.

Run the synthetic unit tests from the repository root:

```text
py -3.13 -B -m unittest discover -s tests/component4 -v
```

This research prototype is not suitable for real emergency operations.
