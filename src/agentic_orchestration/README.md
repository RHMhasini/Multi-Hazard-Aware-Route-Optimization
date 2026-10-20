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

`tests/component4/test_orchestration.py` contains synthetic provider and
authority fixtures reused by the controller tests. Its fixed expected hazard, vehicle and metric records
are built separately from provider claims. These are labelled test fixtures,
not genuine trusted upstream records or evidence of scientific accuracy.

This is deterministic workflow orchestration, **not agentic AI behavior**: it
follows fixed transitions and retry rules. Future agentic experiments could
compare permitted tool-planning strategies against this baseline while retaining
the same deterministic gate. No LLM, LangGraph, multi-agent framework, dashboard
or API is introduced. There is no backoff, hard per-call interruption, cancellation
or live re-routing. Real upstream verification and an authoritative operational
clock source remain integration tasks; tests use synthetic clocks and records.

## Bounded controller and scripted planner baseline

`agent_controller.py::run_controller()` adds a single bounded action loop. The
host supplies the request, ranking policy, provider, verification dependencies,
evidence-bundle identity, shared execution budget, evaluation mode/time/clock,
planner implementation identity, retry policy and maximum action count. None of
these dependencies or safety settings is accepted through a planner action.
The existing request/policy preflight runs before planning or provider I/O.

`Planner.propose(PlannerObservation)` proposes exactly one `PlannerAction` at a
time. Observations are frozen summaries: step/state, identity labels (truncated
to 128 characters for observation only), missing snapshot-input names, allowed
actions, and candidate count. They contain no raw evidence, candidate route IDs,
policy weights, hard constraints, tools, verifiers, clock or budget object.
The full original identities remain in the host trace. The closed allowlist is:

- `acquire_alternatives`: no arguments. Calls `acquire_mission()` through the
  existing typed provider interface, guarded response handling, snapshots,
  transient-only retry policy and provider-call budget. This stage never ranks.
- `evaluate_alternatives`: no arguments and valid only after acquisition. Calls
  `evaluate_mission()` on the controller-held snapshot, without another provider
  call/reservation. Both new stage functions and the unchanged `run_mission()`
  entry point use one execution implementation. Only its existing
  `rank_candidates()` gate can select, including all hard/evidence/metric checks
  and current-mode completion freshness checks.
- `request_clarification`: one typed topic (expected graph snapshot, expected
  hazard snapshot, or host review). Terminates with `unresolved_input`; it does
  not invent missing values or suspend for a reply.
- `stop`: no arguments. Also terminates with `unresolved_input`, never a choice.

Repeated action kinds, evaluation before acquisition, acquisition with missing
snapshot inputs, unsupported actions and arguments are terminal rejections.
There is no action for selecting a route, changing limits/weights/snapshots,
marking evidence verified, replacing authority data or resetting the budget.
Malformed dictionaries are rejected rather than coerced; the audit retains
only a bounded projection of their action name and argument names, not values.
Host configuration errors raise `ValueError`; planner/tool exceptions are
recorded as terminal failures. Process-level termination exceptions propagate.

`ScriptedPlanner` asks for clarification when required snapshot inputs are
missing, otherwise acquires then evaluates according to the observed state.
It is deterministic, observation-dependent workflow planning, **not an LLM
agent**, and does not establish a research advantage over the existing runner.
Evaluation outcomes are terminal, including insufficient evidence and no
eligible candidates; the planner cannot retry evidence failures to force a pass.

The frozen `c4.agent-controller/1.0.0` trace records sanitized proposals,
accepted/rejected actions, observations, next states, full stage tool traces,
errors, dependencies, host limits/budget checkpoints and final outcome.
`trace.selected_route_id` is available only for a final `selected` outcome.
Nested stage rankings remain provisional if any later host gate withholds
selection. `trace.evaluation` can be passed to `replay_evaluation()` under the
existing historical-replay rules; there is no automatic controller/action replay
or durable trace storage. Fixed clocks/inputs/planner/budget observations and the
same independently retained synthetic authority allow deterministic tests.

`BudgetGuard` is shared with the mission runner to validate budget responses.
The host retains the **same budget object across the entire controller session
and subsequent run IDs**. Planning is checked before/after each proposal, tools
before/after execution, with additional reservations/checks inside the runner.
The host action limit bounds planning locally; the shared deadline and provider
call count survive a new run ID. Neither counter is controlled by the planner.
Synchronous planner/provider/verifier calls cannot be forcibly interrupted: an
overrun is detected after return and blocks subsequent actions or release.
Untrusted Python callbacks are not sandboxed by these frozen contracts. A future
remote planner needs host-enforced transport timeouts or process isolation;
deployment-wide durable budgets remain a separate task.

A future ReAct-style LLM planner can replace only `Planner.propose()` behind a
strict closed-schema decoder: observe a sanitized summary, propose an allowed
action, receive the next summary. The host executes permitted tools and owns the
decision. Do not grant the model direct tool handles, Python execution, verifier
or budget wiring, raw instructions from upstream artifacts, or authority to
change a mission. There is no LLM provider, prompt implementation, reasoning
trace, API, UI, multi-agent system or production adapter in the controller baseline.
Real authoritative artifacts and clocks remain integration work; synthetic
fixture authority is not evidence of real-world trust or safety.

## Structured planner adapter

`llm_planner.py::StructuredLLMPlanner` implements the planner interface through a
provider-independent `ModelClient.complete(ModelRequest) -> ModelReply` protocol.
The adapter itself is provider-independent; the optional local HTTP transport
is described below. It needs no provider SDK, API key or agent framework.
`ScriptedPlanner` remains the deterministic experimental baseline. The fake clients exist only in
`tests/component4/test_llm_planner.py` and do not demonstrate model intelligence,
prompt-injection resistance or an agentic research advantage.

The versioned JSON action envelope is deliberately small:

```json
{"schema_version":"c4.planner-action/1.0.0","kind":"acquire_alternatives"}
```

```json
{"schema_version":"c4.planner-action/1.0.0","kind":"request_clarification","clarification":"host_review"}
```

Only `schema_version` and `kind` are allowed for acquire/evaluate/stop.
Clarification additionally requires one of the existing typed topic strings.
Even `clarification:null` on other actions is rejected. `decode_action()` checks
the UTF-8 byte limit before parsing, then rejects unknown versions/actions,
duplicate keys (including escaped duplicates and nested objects), extra/missing
fields, invalid value types, arrays/multiple actions, surrounding prose/code
fences, nonstandard NaN/Infinity constants and malformed/deeply nested JSON.
Ordinary JSON whitespace is allowed. No fields are filtered out, repaired or
inferred, and model text is never executed.

The decoder returns the existing `PlannerAction`. The adapter wraps it in
`ModelProposal` with its call audit; the controller unwraps it and still applies
its independent action allowlist, argument types, state and repetition checks.
Decoding success cannot establish eligibility or select a route. Missing,
stale, mismatched or unsupported evidence still encounters the same deterministic
ranking/verification gate. No model output configures a constraint, weight,
snapshot, verifier, evaluation time, budget or route ID.

`ModelRequest` keeps fixed `instructions` separate from canonical
`observation_json` data. Both are immutable strings, with prompt/decoder versions
and generation settings recorded. Labels remain untrusted even when JSON-encoded;
the model may follow embedded malicious instructions. A future transport must
preserve this instruction/data separation and must not interpolate observation
labels into privileged instructions. The host's closed action surface and safety
gate remain necessary; prompt separation is not a semantic security guarantee.

The host enables model calls explicitly with the existing budget:

```python
budget = HostExecutionBudget(
    "experiment-session", max_provider_calls=4, max_duration_sec=60,
    max_model_calls=4, max_model_output_bytes=4096,
)
planner = StructuredLLMPlanner(fake_client, model_id="fake/model", budget=budget)
# Pass this exact budget object to run_controller(..., planner, budget=budget).
```

Model calls default to zero (disabled). The shared atomic budget now tracks
provider calls, model calls, response-byte limit and total duration. The built-in
adapter/controller reject different budget objects. Reusing the same budget
across new run IDs or new adapter objects does not reset either counter or the
deadline. Budget reservations are validated before client I/O, including a check
that the model counter actually increments. Exhaustion and inconsistent budget
responses fail closed. Older custom budgets can continue serving scripted runs,
but must implement model reservations before supporting this adapter.

Every proposal permits **one client attempt**, `attempt=1, max_attempts=1`.
There is no internal retry, fallback model or output-repair request. Each later
proposal reserves another shared model call. `ModelClient` is trusted host
transport wiring and must not hide retries; this interface cannot inspect an
arbitrary implementation's network behavior. Future transport retries require
explicit host accounting, not SDK defaults.

Requests carry the shared scope/call index, elapsed time at reservation, deadline
relative to budget start, remaining `timeout_sec`, generation token limit and
response-byte limit. These are cooperative limits, not hard interruption.
Blocking clients can overrun; the adapter checks the budget after return and
before releasing a decoded proposal. A future live transport must implement
timeouts and bounded/streamed reads itself. The adapter checks response size
after the client supplies text; it cannot prevent a client from allocating an
oversized reply. No total-token/cost quota or process isolation is implemented.

Refusals, truncation, malformed replies, decode failures and ordinary client
exceptions terminate safely without retry or selection. Process termination
exceptions propagate. Arbitrary client error messages are not copied into the
trace, to avoid recording transport secrets. Shared budget failures become a
controller `budget_exhausted` outcome; other adapter failures become
`planner_failure`, with the specific diagnostic in the model-call record.

Each controller `ActionRecord` now has optional `model_call` metadata, leaving
scripted actions with `None`. The frozen `c4.model-call/1.0.0` record contains:
model ID/reported revision, prompt/decoder versions, copied generation settings,
immutable request, attempt, host-measured cooperative call latency in seconds,
reported usage when available, refusal/truncation flags, outcome, budget
checkpoints and a bounded response's byte count/SHA-256 when available. Unknown
metadata remains `None`. Latency uses shared monotonic budget observations and
includes checkpoint overhead; it is not provider-reported inference time.
Model identity, revision, usage and hashes are audit metadata, not proof of
authenticity, trust or safety. Traces remain in-memory and are not tamper-proof.

Raw responses are not retained in controller traces. Exact decoder replay needs
independently retained response fixtures/artifacts in addition to metadata; a
hash alone cannot reconstruct text. Model regeneration is not guaranteed to be
reproducible even with a seed. Existing `replay_evaluation()` still replays only
deterministic evaluation at recorded historical time, never current safety.

## Optional local Ollama HTTP transport

`ollama_client.py::OllamaModelClient.complete(ModelRequest) -> ModelReply` uses
only standard-library HTTP/JSON tools. It performs exactly one non-streaming
`POST /api/chat` per call, with `format: "json"`, `think: false`, and no tools.
It does not start Ollama, install an SDK, download/select a model automatically,
retry, follow redirects, repair responses or make fallback calls.

The constructor accepts a host-configured origin (default
`http://127.0.0.1:11434`) and a separate `max_http_response_bytes` body-envelope
cap (default 65536 bytes). Only numeric loopback addresses and `localhost` are
accepted; localhost is mapped to 127.0.0.1 without DNS. IPv6 `::1` is supported.
Remote/LAN hosts, credentials, path prefixes, query strings, fragments and HTTPS
are rejected. Standard-library HTTP does not use environment proxy settings.
The endpoint is never taken from a model action or mission observation.

Fixed planner instructions become the system message; serialized observations
remain separate user-message data. Temperature, output-token limit and optional
seed map to `options.temperature`, `options.num_predict` and `options.seed`.
Servers/models that do not support the requested behavior fail rather than
triggering an unrecorded fallback. JSON mode assists generation; the existing
strict action decoder remains the final acceptance boundary. Role separation
does not guarantee protection from prompt injection.

The transport derives a monotonic deadline from `request.timeout_sec` and checks
it across serialization, connection, sending, response headers, body reads and
response validation. Socket timeouts are updated to the remaining duration on
each underlying read/write, including reads used by HTTP header parsing, so a
slow trickle does not reset the total deadline. Sockets/files close on success
and ordinary failures; process-level termination exceptions still propagate.
The outer shared budget independently checks elapsed time before action release.
Timeouts cannot guarantee exact OS scheduling or cancellation of Ollama's
server-side inference; closing the client connection is not proof of cancellation.

HTTP status must be 200 with a supported JSON content type. Compression,
ambiguous framing and duplicate critical headers are rejected. Chunked HTTP
transfer is supported for a single JSON body; model NDJSON streaming is not.
Application body accumulation is bounded to the envelope cap plus a one-byte
oversize sentinel, before UTF-8 decoding/JSON parsing. The HTTP parser additionally
uses bounded standard-library header/read buffers. Declared oversized lengths are rejected before body
reading; incomplete bodies, duplicate JSON keys, nonstandard constants, multiple
objects and malformed JSON fail closed. The generated action text separately
must fit `request.max_output_bytes`; token limits are not byte limits.

Response policy is deliberately conservative: exact requested model ID,
`done: true`, `done_reason: "stop"` or `"length"`, assistant role and text content.
`length` produces `ModelReply.truncated=True`, causing adapter abstention even
if the action text is parseable. Unknown envelope/message fields, finish reasons,
tool/image outputs and nonempty thinking output are rejected. Aliased model names
that the server normalizes differently will fail the identity check; configure
the exact canonical model tag instead of silently rewriting the response.

Native chat provides no dedicated refusal flag or verified model revision in
this supported response shape. Revision remains `None`; no explicit refusal
signal is fabricated. Natural-language refusals are passed unchanged and fail
strict action decoding. Unsupported explicit refusal fields/reasons are rejected,
not ignored. Reported prompt/output token counts are validated as nonnegative
integers. If either count is absent, combined usage remains `None`; missing counts
are never invented as zero. Provider identity/usage remain audit assertions, not
artifact verification or proof of safety. A loopback endpoint does not authenticate
the local process or prove which model weights it loaded.
It also cannot prevent a local service from forwarding data elsewhere; the host
must ensure its server/model configuration actually performs local-only inference.

Setup prerequisites: the user independently manages a running local Ollama
server and an already available model supporting chat/JSON mode and the requested
options. No model or hardware choice is assumed. Set the model ID in host wiring,
retain the same host budget for the adapter/controller, and inject
`OllamaModelClient()` in place of the fake client. No controller, validation,
ranking or Components 1–3 changes are required.

### Optional manual local smoke test — NOT part of automated tests

Only run this yourself when you intend to contact your local server. From the
repository root, in a Python session, set `model_id` to an exact already-installed
model tag and execute the following. This makes one real model request for a
synthetic stop-only observation; it does not generate routes or dispatch a mission.
No installation/download/start-server commands are part of this procedure.

```python
from src.agentic_orchestration import (
    ActionKind, ControllerState, HostExecutionBudget, OllamaModelClient,
    PlannerObservation, StructuredLLMPlanner,
)

model_id = "YOUR_ALREADY_INSTALLED_MODEL:TAG"
budget = HostExecutionBudget(
    "manual-local-smoke", max_provider_calls=0, max_duration_sec=60,
    max_model_calls=1, max_model_output_bytes=4096,
)
planner = StructuredLLMPlanner(OllamaModelClient(), model_id=model_id, budget=budget)
observation = PlannerObservation(
    1, ControllerState.READY, "synthetic-request", "synthetic-mission",
    "synthetic", "synthetic-policy", "1.0.0", (), (ActionKind.STOP,), None,
)
proposal = planner.propose(observation)
assert proposal.action.kind is ActionKind.STOP
print(proposal.model_call.outcome)
```

The stop assertion is a smoke-test expectation, not evidence of safety or model
quality. Direct `propose()` does not replace controller state/allowlist checks;
mission decisions must still use `run_controller()`. A 60-second timeout in this
example is an experiment setting, not an operational routing threshold.

All transport tests in `tests/component4/test_ollama_client.py` mock HTTP/socket
data. They cover deadline behavior, caps, response validation, cleanup and an
end-to-end controller/adapter run with separately held synthetic authority.
No live request was used to validate this milestone. Compatibility with a
particular installed Ollama version/model remains unverified until an optional
manual test. Strict response-field checks may reject future API extensions;
review supported formats explicitly rather than silently relaxing them.

## Optional Groq HTTPS transport

`groq_client.py::GroqModelClient.complete(ModelRequest) -> ModelReply` implements
the same `ModelClient` protocol using Python's standard library. It sends one
non-streaming HTTPS POST to the fixed official endpoint
`https://api.groq.com/openai/v1/chat/completions`, using the exact model ID
`openai/gpt-oss-20b` (`GROQ_MODEL_ID`). No SDK or extra framework is required.
Construction/import does not contact Groq; `complete()` does.

The model ID and parameters were checked against Groq's official documentation
on 2026-10-20: [model details](https://console.groq.com/docs/model/openai/gpt-oss-20b),
[chat API reference](https://console.groq.com/docs/api-reference), and
[reasoning options](https://console.groq.com/docs/reasoning).
Generation settings map to `temperature` (0 through 2),
`max_completion_tokens`, and optional `seed`. Fixed transport settings are
`response_format: {"type": "json_object"}`, `stream: false`,
`reasoning_effort: "low"`, and `include_reasoning: false`.
`reasoning_format` is not supported for this model and is not sent.
No tools, search, model fallback, redirects, proxy environment settings,
automatic retries or response repair are used.

JSON mode assists generation; it does not enforce the closed action schema.
The existing `decode_action()` still rejects extra fields, route selections,
policy/constraint/snapshot changes, duplicate keys and invalid action values.
The controller independently checks action state, repetition and its allowlist.
Groq receives the fixed planner instructions as a system message and the bounded
observation as a separate user message. This is role separation, not proof of
prompt-injection resistance. Verifiers, route evidence, ranking and shared
execution budgets remain host-owned and unchanged.

### Key configuration and host wiring

Store `GROQ_API_KEY` in your environment, never in source, a tracked environment
file, a prompt or an observation. Do not print it or include it in diagnostics.
The client reads it at call time: the process variable takes precedence; on
Windows, if absent, it reads only `GROQ_API_KEY` from the current user's
`HKCU\Environment`. This supports VS Code instances started before the user
variable was saved. An existing invalid/empty process value fails rather than
silently selecting another credential. No registry/environment values are
written, and the key is not stored on the client or in planner audit records.

To explicitly refresh an old PowerShell terminal from an already-configured
Windows user variable, without displaying the credential:

```powershell
$env:GROQ_API_KEY = [Environment]::GetEnvironmentVariable('GROQ_API_KEY', 'User')
```

Restarting VS Code is another way to inherit updated variables. Missing or
invalid keys fail before network access. The client does not verify whether an
existing key is active until an explicitly authorized request is made.

Host wiring (this example constructs objects only; it makes no API call):

```python
from src.agentic_orchestration import (
    GROQ_MODEL_ID, GenerationSettings, GroqModelClient,
    HostExecutionBudget, StructuredLLMPlanner,
)

budget = HostExecutionBudget(
    "groq-experiment", max_provider_calls=1, max_duration_sec=60,
    max_model_calls=2, max_model_output_bytes=4096,
)
planner = StructuredLLMPlanner(
    GroqModelClient(), model_id=GROQ_MODEL_ID, budget=budget,
    settings=GenerationSettings(temperature=0, max_output_tokens=1024),
)
# When authorized, inject planner and this SAME budget into run_controller(),
# together with trusted provider/verifier dependencies and synthetic inputs.
```

Groq is a remote service: only use observations approved for external processing.
Do not put secrets or sensitive mission labels in observations. A live call is
an explicit host action, not part of setup or automated tests. This adapter
does not use the local Ollama model or its D: storage.

### Limits, failures and verification scope

The [documented free-plan limits](https://console.groq.com/docs/rate-limits)
for this model at the verification date are 30 requests/minute, 1,000
requests/day, 8,000 tokens/minute and 200,000 tokens/day. These can change and
organization-specific exceptions apply; check your account's Limits page.
Free access is quota-limited and not a capacity/availability guarantee.
Reasoning can consume the completion-token allowance even when hidden;
small output limits may produce truncation. Seeded generation is best-effort,
not guaranteed deterministic. Host-side route ranking remains deterministic.

Each `complete()` makes at most one request. A 429 is a fixed
`groq_rate_limited` failure; the client neither sleeps nor retries, even if
`Retry-After` is present. Authentication/other HTTP errors also fail without
reading or exposing the error body. Any later experiment needs separate host
authorization and the existing shared budget; do not reset it to bypass limits.

Verified TLS, an envelope byte cap (default 65,536), a separate action-text byte
cap, bounded reads and a decreasing monotonic deadline protect the transport.
Checks include reads during HTTP header parsing and body transfer. DNS resolution,
OS scheduling and server-side cancellation cannot be forcibly bounded by this
synchronous implementation; the shared budget checks elapsed time again before
releasing an action. No client deadline implies server-side cancellation.

Malformed JSON/UTF-8, duplicate keys, ambiguous framing, compressed/streamed
formats, model mismatches, multiple choices, unexpected reasoning/tool calls,
and unsupported statuses fail closed. Explicit refusals/content filtering and
length truncation return flags that the planner rejects, even if the content
looks like a valid action. Natural-language refusal fails strict JSON decoding.
Usage counts are retained when supplied and valid, otherwise unknown; invalid
counts are rejected. `system_fingerprint` is backend metadata, not a verified
weight revision, so `model_revision` remains unknown. Provider metadata is an
audit assertion, not evidence authentication.

`tests/component4/test_groq_client.py` mocks HTTPS/socket data and credentials,
including Windows user-variable fallback, rate limits, deadlines, byte caps,
refusal/truncation, shared-budget exhaustion and a controller run with separately
held synthetic authority. No live API call has been made; real key access,
account permissions, model behavior, latency and current service compatibility
remain unverified. Future API extensions may require explicit response-parser
updates. These tests are not evidence of model quality or operational safety.

Run the synthetic unit tests from the repository root:

```text
py -3.13 -B -m unittest discover -s tests/component4 -v
```

This research prototype is not suitable for real emergency operations.
