# Component 4: Implementation and Evaluation Roadmap

Date: 2026-10-21. Status: **PROPOSED**; no development authorization is implied.
Baseline: `feature/component4-agentic-ai`, `94b148c`.

Read the [evidence review](research-evidence-review.md),
[ADRs](architecture-decision-records.md), and [blueprint](adaptive-agent-blueprint.md).
All new module paths below are proposed files, not existing implementations.
No experiment results are invented in this document.

## 1. Current evidence and development constraints

The baseline has 194 passing Component 4 automated tests. Existing transport tests
mock HTTP; synthetic authorities simulate independent evidence. Prior session
observations recorded three valid live Groq action choices. Those calls establish
basic connectivity and interface compatibility, not adaptive recovery, field
validity or model superiority. No live model call is part of this documentation
milestone. Components 1-3 remain unchanged.

Maintain the current v1 route/evidence/ranking gate and its tests. Add adaptive
contracts beside v1 before migrating callers. Every milestone needs explicit
review, a focused diff and separate validation; documents are not approval to
execute external jobs. Expensive source preparation, live APIs, spending and real
mission disclosure require their own authorization. Do not install a framework
merely to create an agent-labelled architecture.

## 2. Small implementation milestones

Each milestone has its own acceptance/rollback boundary. Named source/test paths
are repository-relative. Existing files are identified as such where relevant.

### M1. Dependency observations and pure eligibility

**Purpose:** represent fresh/missing/stale/unsupported dependencies and derive
meaningful next-tool options without executing tools or contacting an LLM.

- Modules: proposed `src/agentic_orchestration/adaptive_contracts.py`,
  `src/agentic_orchestration/dependency_state.py`, and
  `tests/component4/test_dependency_state.py`.
- Inputs/dependencies: blueprint enums and immutable synthetic state; current
  mission/policy identity types; agreed distinction between availability,
  freshness, coverage and verification.
- Outputs: closed typed dependency reports and a deterministic eligibility
  function. No network, persistence or model schema migration yet.
- Tests: fresh complete inputs permit generation/evaluation as appropriate;
  missing hazard permits only supported targeted preparation/clarification;
  simultaneous missing graph/hazard permits either independent valid order;
  unsupported preparation does not appear as executable.
- Acceptance: malformed/non-finite/contradictory state rejected; unchanged inputs
  produce identical options; at least two contrasting states produce different
  useful choices; all existing 194 tests remain unaffected and pass.
- Failure/rollback: leave v1 imports/controller untouched and remove/disable the
  new module path if semantics cannot be agreed. Do not fabricate source validity.
- Research basis: R02/R05 (see evidence review); engineering decision ADR-05.
- Dissertation evidence: schema rationale, state-transition examples, test cases
  and explicit assumptions; no claim of model autonomy from this milestone.

**M1 is the single smallest recommended coding milestone after design review.**

### M2. Synthetic tools and independent authority

**Purpose:** create a controllable environment for real tool-state changes.

- Modules: proposed `tool_registry.py`, `synthetic_tools.py`,
  `tests/component4/test_tool_registry.py`, `tests/component4/synthetic_oracles.py`.
- Inputs/dependencies: M1 contracts, frozen small directed keyed-edge graphs,
  raster/coverage fixtures, scripted version changes and fault schedules.
- Outputs: registered typed tools, idempotency identities, explicit unsupported
  results and separately held artifact/metric authority. No real source writes.
- Tests: each tool's preconditions, stale option rejection, failure taxonomy,
  NoData, coordinate mismatch, missing vehicle restrictions and forged metrics.
- Acceptance: actual adapter calls are countable; a forged output cannot grant
  authority; oracle data is not obtained by echoing tool results; snapshots remain
  immutable. Small-graph route metrics agree with an independent enumerator.
- Failure/rollback: disable a failing tool and return unsupported; retain usable
  tools and the existing deterministic gate.
- Research basis: R02, R05, R11 and ADR-08.
- Dissertation evidence: simulator specification, trust boundaries, fault-injection
  trace, oracle review and a statement that synthetic truth is not field truth.

### M3. Adaptive deterministic controller and budgeted feedback

**Purpose:** implement the state machine and a strong non-LLM adaptive comparator.

- Modules: proposed `adaptive_controller.py`, `rule_planner.py`,
  `tests/component4/test_adaptive_controller.py`; backward-compatible extensions
  to existing `execution.py` only if reviewed for a unified reservation ledger.
- Inputs/dependencies: M1-M2, typed tool feedback and the existing validators,
  `RankingPolicy`, `EvidenceVerifier` and `RankingMetricVerifier`.
- Outputs: dynamic options, visible retries, invalidation and an A/B planner
  interface. Preserve `run_controller()` v1 until migration is approved.
- Tests: reuse current evidence, targeted hazard-only refresh, graph-change
  invalidation, retryable/permanent failures, repeated no-progress and deadlines.
- Acceptance: three distinct valid execution paths; no hidden retries or budget
  reset across run IDs; original gate remains final; evaluation failures return
  actionable feedback without relaxing limits; nested calls are metered once.
- Failure/rollback: switch off adaptive entry point, retain v1 fail-closed path.
  A recovery bug must not change ranking/evidence code to make a case pass.
- Research basis: R05/R12 and ADR-02/03/09.
- Dissertation evidence: transition coverage, action traces and fair deterministic
  baseline results, including unsuccessful missions.

### M4. Versioned adaptive LLM planner

**Purpose:** substitute an LLM for next-action choice without changing the tools.

- Modules: proposed `adaptive_planner.py`, `tests/component4/test_adaptive_planner.py`;
  reviewed backward-compatible metadata/version extension to existing
  `llm_planner.py`; Groq/Ollama transport tests if request metadata changes.
- Inputs/dependencies: M3 observation/options, fixed adaptive instructions,
  strict v2 action schema, existing `ModelClient` and controlled model identity.
- Outputs: one decoded, state-bound option per model attempt and scoped audit
  metadata. Existing v1 defaults and decoder remain supported.
- Tests: forged option IDs, policy numbers, unknown keys, model refusal,
  truncation, stale revisions, malicious feedback and transport errors.
- Acceptance: no v2 request audited as v1; same tools/permissions/budgets as rule
  planner; malformed output cannot invoke a tool. Mock first; any live free-tier
  experiment is separately authorized and stops on authentication/quota failure.
- Failure/rollback: retain the adaptive rule planner; never silently use another
  provider or make a second model attempt outside the ledger.
- Research basis: R01/R03/R11 and ADR-04/10.
- Dissertation evidence: prompt/version hashes, model/backend metadata where
  available, invalid-action distribution, usage and response-latency records.

### M5. Durable state, verified reuse and human continuation

**Purpose:** support interruption and continuation without resetting trust/budgets.

- Modules: proposed `mission_store.py`, `human_review.py`,
  `tests/component4/test_mission_store.py`, `tests/component4/test_human_review.py`;
  existing `snapshots.py` only if new immutable types need registration.
- Inputs/dependencies: M3 state, transactional local storage, idempotent adapters,
  authenticated human decision boundary and reviewed retention policy.
- Outputs: compare-and-swap state transitions, durable reservations, immutable
  artifact refs, scoped approval/clarification tokens and cache dependency keys.
- Tests: crash before/after tool dispatch, concurrent/stale replies, replayed or
  expired tokens, denied approval, changed hazard while suspended and cache reuse.
- Acceptance: uncertain side effects reconciled before replay; old artifacts never
  rebound to new snapshots; resume preserves counters and rechecks evidence age.
- Failure/rollback: suspend and reconcile or abstain; use a new explicitly
  authorized mission rather than corrupting the old ledger.
- Research basis: R02/R10 and ADR-03/09/10.
- Dissertation evidence: crash matrix, storage schema, resume audit and timing
  definition separating active processing from human waiting.

### M6. Approved policy registry and sensitivity diagnostics

**Purpose:** make mission preferences explicit without LLM-generated weights.

- Modules: proposed `policy_registry.py`, `sensitivity.py`,
  `tests/component4/test_policy_registry.py`, `tests/component4/test_sensitivity.py`.
  Existing `ranking.py` stays unchanged for the planner experiment.
- Inputs/dependencies: approved mission applicability and expert judgments,
  normalized criterion meanings, policy versions and permitted uncertainty ranges.
- Outputs: approved profile lookup/proposal, explicit activation approval and
  separate route-rank stability diagnostics. No new criteria in v1 ranking.
- Tests: ambiguous mission/profile, version expiry, invalid weights, policy-only
  reuse, policy-dependent candidate invalidation and controlled rank reversals.
- Acceptance: profile activation is authenticated and audited; a model cannot
  insert numeric weights; sensitivity never silently substitutes a new ranker.
- Failure/rollback: unknown policy leads to review; retain a previously valid
  profile only if its applicability still holds, otherwise abstain.
- Research basis: R08/R09/R13/R14 and ADR-06/07.
- Dissertation evidence: elicitation protocol, disagreement/consistency records,
  approvals and sensitivity plots. Any human study needs required institutional review.

### M7. Real component adapter feasibility pilots

**Purpose:** replace one synthetic binding at a time without overstating maturity.

- Modules: proposed `component_adapters.py` or a reviewed `adapters/` package,
  `artifact_verification.py`, `tests/component4/test_component_adapters.py`.
  C1-C3 changes are separate owner milestones, not part of C4 authorization.
- Inputs/dependencies: owner-approved artifacts; C2 graph availability; C1 model
  limitations; C3 CRS, coverage, keyed-edge and precision mappings; trusted OD data.
- Outputs: explicit supported/unsupported capabilities, manifests and independent
  verifiers. Existing `RouteAlternativesProvider` is the v1 bridge, not a new C3 API.
- Tests: real-format saved artifacts with intentionally corrupted tags/CRS/keys;
  C3 unknown-as-zero and safety-relaxation paths must not pass C4 verification.
- Acceptance: exact origin-to-route edge bindings, source identity and unknown
  handling survive conversion; missing authority remains a blocker. Synthetic
  hazard training remains labelled even if the graph is real.
- Failure/rollback: keep synthetic-only capability enabled and report unsupported
  real integration. Do not loosen hard constraints or manufacture timestamps.
- Research basis: R06/R07 and ADR-05/08.
- Dissertation evidence: adapter conformance reports, artifact hashes, missing
  upstream requirements and precise claims about which data is synthetic/real.

### M8. Frozen comparative experiment and analysis

**Purpose:** test RQ1-RQ5 on a locked benchmark and record negative findings.

- Modules: proposed `experiments/component4/run_benchmark.py`,
  `experiments/component4/analyse_results.py`, versioned scenario/oracle manifests
  and `tests/component4/test_benchmark_oracles.py`. Files are not created now.
- Inputs/dependencies: reviewed M1-M6 behavior, held-out scenarios, frozen profiles,
  tools, budgets, prompts and statistical analysis plan. M7 is not required for
  a correctly labelled synthetic study.
- Outputs: redacted machine-readable run records, aggregate tables, confidence
  intervals, failure taxonomy and reproducible analysis artifacts.
- Tests: denominator checks, event schedule equivalence, no hidden calls,
  independent oracle scoring, incomplete/quota-blocked run handling and replay.
- Acceptance: A/B/C differ only in planning; no tuning on held-out outcomes;
  results include failures, safety attempts and uncertainty, not only successes.
- Failure/rollback: quarantine invalid experimental runs with reasons; fix the
  protocol and preregister a new version rather than selectively discard losses.
- Research basis: R02/R03/R04/R05/R11 and all relevant ADRs.
- Dissertation evidence: protocol, environment manifest, all run dispositions,
  code/data versions, approvals, raw redacted events and exact analysis commands.

## 3. Controlled A/B/C experiment

| Variant | Planner definition | What must remain identical |
| --- | --- | --- |
| A: fixed workflow | A declared topological stage order: inspect, hazard preparation, network preparation, alternatives, evaluation; terminates/clarifies when its fixed policy cannot continue. | Same tool registry, host cache, typed errors, verification, profiles, hard limits, outputs and maximum resources. |
| B: adaptive deterministic | Explicit condition rules choose targeted preparation, verified reuse, bounded retry or review based on feedback. | Same observation fields and available options as C. |
| C: adaptive LLM | Chooses one option from the same state-dependent set using the versioned prompt. | Same execution engine; no extra tools, numeric authority or relaxed limits. |

B is a proposed new rule planner, **not** a claim that the existing
`ScriptedPlanner` already performs adaptive recovery. A must have a useful happy
path and identical cache access; do not artificially disable caching/verification
to make it lose. A fixed order can still receive a cache hit, but requesting an
unneeded stage remains observable. B may be a strong winner; that is informative.

All variants obey identical host preconditions. Ineligible requests are rejected
and counted, not executed. Count planner-requested operations and actual underlying
executions separately. Otherwise a cache could hide a planner's redundant choices.
The shared host includes the same approved generic recovery rules; planner-specific
branch decisions must not be moved into hidden variant-specific adapters.

Initial shared ceilings follow the blueprint: 8 model calls, 12 tool attempts,
20 steps and 120 active seconds; one explicit retry per unchanged tool/input
identity. Rules naturally use zero model calls; equal maximum permissions do not
mean artificially charging them tokens. Report both equal tool-budget results and
resource/outcome trade-offs. Retain counters across clarification and new run IDs.

Use virtual-clock experiments to isolate decision logic and measured wall-clock
experiments to assess actual latency; never mix those durations in one average.
Future provider measurements need separate free-tier authorization. A 429/auth
failure stops the affected provider session without retry or upgrade, records the
blocked disposition, and is not silently replaced by a different model.

## 4. Scenario families and independent oracles

The proposed split has at least two development instances per family (22 total)
and four held-out instances per family (44 total). These are design targets, not
collected samples or a statistical power claim. Use five paired repetitions as
an initial variability study, with final sample size reviewed after a development
pilot and before opening held-out results. More calls may exceed free quotas;
plan authorized sessions and costs in advance, never work around rate limits.

Split by graph/layout, failure location and event combination, not merely new
mission names. Held-out seeds, exact states and oracle manifests stay outside
planner prompts and development tuning. Preserve extreme/boundary cases. A
separate reviewer should examine oracle correctness before evaluation.

`Oracle(state)` returns permitted *useful* next-action sets, acceptable terminal
outcomes, prohibited transitions and independently computed route evidence.
Host eligibility is necessary but not sufficient for oracle usefulness: stopping
or asking an irrelevant question may be permitted by the host yet fail the task.
The oracle is deterministic code/fixtures independent of the evaluated planner,
not an LLM judge or the same adapter echoing its own metrics.

| Family | Ground truth and expected behavior | One next tool or multiple paths? |
| --- | --- | --- |
| Fresh complete evidence | Pinned valid graph/hazard and verified compatible alternatives. Evaluate directly; expected ranking from independently enumerated small-graph metrics. No preparation needed. | One useful next tool: evaluation. |
| Missing hazard data | Valid graph, absent hazard; approved synthetic hazard source exists in one variant. Prepare hazard, then generate/evaluate. Another variant has no supported source and must clarify/abstain. | Singleton preparation when available; multiple legitimate terminal review paths otherwise. |
| Stale road graph | New approved network source available; hazard remains valid. Prepare graph and invalidate/rebuild routes. Include a variant with both graph and hazard stale. | Singleton graph preparation, or either independent preparation order in the combined variant. |
| Changed graph snapshot | Deterministic graph update after candidate generation invalidates one route binding. Old results cannot be released; adopt/rebuild/evaluate according to the frozen update policy. | Multiple preparation/reconciliation paths, all respecting the new revision. |
| Road closure | Synthetic authoritative closure manifest removes a known keyed directed edge. Enumerated graph defines remaining reachability/routes; do not equate a susceptibility threshold with real closure observation. | Several route searches may be valid; all must exclude the closed edge. |
| Transient tool failure | First designated tool attempt fails, second can succeed; another approved equivalent source may exist. Expected recovery within the shared ledger. | Retry or equivalent source are both valid when explicitly authorized. |
| Permanent tool failure | Unsupported source/adapter or permanent failure, with and without an alternate approved source. Never repeat the same doomed call. | Alternate path if present; otherwise justified review/abstention. |
| Changed mission priorities | Authenticated human selects an applicable preapproved profile, while hard limits stay fixed. Re-evaluate unchanged policy-independent candidates; regenerate if their search profile is incompatible. | Branch depends on independently recorded search-policy binding. |
| Infeasible mission | All paths in the small oracle graph violate an immutable hard limit, or OD is disconnected. Expected abstention; no relaxed threshold or invented evidence. | Multiple valid discovery paths; no recommendation. Distinguish complete-graph oracle truth from candidate-only tool claims. |
| Human clarification/continuation | OD or policy applicability ambiguous; scripted authenticated reply resolves it, is denied or expires. Resume requires new state revision, retained budgets and fresh checks. | Several reasonable clarification topics may be valid; reply/revision oracle determines continuation. |
| Contradictory/malicious tool output | Valid-looking envelope contains mismatched snapshot/metric data or instruction text to override constraints/disclose secrets. Independent authority detects corruption; text grants no permission. | Quarantine, approved alternative, review or abstention depending on trusted evidence; no unauthorized action is valid. |

For dynamic families, inject changes at declared logical milestones (for example,
after first candidate generation), not an uncontrolled wall-time instant that
would affect slower planners differently. The environment seed and failure
schedule are paired across A/B/C. Hidden state determines results, not whether a
planner happens to choose the experimenter's preferred valid order.

## 5. Metrics, denominators and statistical uncertainty

| Metric | Proposed operational definition |
| --- | --- |
| Correct tool-choice rate | Oracle-useful next choices / scorable planning decisions. Include invalid proposals as wrong. Score appropriate terminal/clarification decisions separately and overall. |
| Mission completion | Valid recommendation released / feasible, sufficiently resolvable missions. Also report correct overall resolution, which includes required abstention. |
| Recovery success | Valid resolution after an injected recoverable failure / recoverable-failure instances; report irrecoverable cases separately. |
| Unnecessary calls | Planner-requested operations the independent oracle deems avoidable, plus actual unnecessary executions; show counts and rate per mission. |
| Safe abstention | Correct withheld recommendations / cases requiring withholding; also report false abstention on feasible/resolvable cases. |
| Safety violations | Attempted forbidden actions, executed forbidden actions and invalid releases as three separate counts. No recommendations in unknown-evidence cases may be counted as safe completion. |
| Latency | Per-model, per-tool, active end-to-end and human-wait duration, plus median, upper quantiles and timeout fraction. Simulated and real clocks are separate. |
| Token/cost | Reported input/output tokens, missing usage count, model attempts and account/provider regime. Monetary estimates need a dated price basis; free quota is not unlimited or zero research effort. |
| Repeated-run variability | Per-instance success fraction, action-path distribution and all-k-success statistic; temperature 0 does not imply repeatability. |
| Ranking sensitivity | Top-route changes, rank correlation and criterion contributions under approved weight/normalization/pool perturbations; not a measure of real route safety. |

Predefine hypothesis direction, primary endpoints (correct overall resolution,
unnecessary calls and executed violations), exclusions and minimum worthwhile
effects. Do not choose a winning metric after inspecting held-out results.
Report per-family and overall results so numerous easy cases cannot hide failures.

For uncertainty, use paired differences on matched instances and cluster bootstrap
confidence intervals at the independent scenario-instance level; repeated trials
are nested observations, not independent new missions. Report bootstrap seed,
replicate count and interval method. For independent-instance binary summaries,
Wilson intervals are a useful supplementary check; do not apply naive binomial
intervals to pooled correlated repetitions. Define multiplicity handling or label
secondary comparisons exploratory. A pilot informs sample size and interval
width; no power claim is made here.

An all-k success estimate can average `choose(c,k)/choose(n,k)` per instance for
`n >= k`, where `c` is successful repetitions. This follows the reliability
distinction in R03; it is not the probability of at least one successful attempt.
Five repetitions support only limited estimates. Zero observed executed
violations does not establish zero population risk; publish the sample size and
appropriate uncertainty bound rather than a guarantee.

Report a complete disposition ledger: completed, invalid-action terminated,
tool/model failed, quota/auth blocked, timed out, human denied and infrastructure
fault. Present both conditional planner analysis and operational availability;
do not remove provider outages without showing their counts and the predeclared
rule. No selective successful-run reporting or retry-until-success scoring.

## 6. Separate preference and ranking experiment

Freeze verified candidates and hard eligibility first. Compare existing
constrained weighted sum with offline TOPSIS and Pareto diagnostics under the
same matrix. Then vary approved weight ranges and normalization policies in a
separate factor; do not change planner and ranker simultaneously and attribute
the effect to agentic reasoning.

For the current two criteria, sweep the time weight through a preregistered grid
within the expert-approved interval and use complementary exposure weight.
Include equal/zero weights only as permitted experiment conditions, not new
operational policies. Compare existing candidate-relative min-max normalization
against fixed reference scales only where experts justify reference values.
Test constant criteria, outliers, dominated additions, duplicate paths and changed
candidate sets. Preserve deterministic route-ID tie behavior in the baseline.

Record recommendation switch points, Kendall/Spearman rank agreement where ranks
are defined, ties, contribution changes and worst-case regret over the stated
weight set. Such regret is conditional on the model and candidates, not real
expected loss. Do not invent uncertainty distributions; begin with bounded
scenario/interval sensitivity. Predefine a human-review rule for unstable output
with domain experts before enabling it in the release gate.

Expert profile elicitation is a distinct study: document criterion meaning,
scale interpretation, participant roles, AHP/BWM consistency, disagreement and
reviewed aggregation. Entropy diagnostics describe a decision matrix; they cannot
replace stakeholder accountability. Two-criterion BWM/AHP elaboration may add
little beyond a carefully elicited ratio. No method is called most accurate
without a task-specific reference and a valid comparison.

## 7. Dissertation package, approvals and reconsideration

Collect repository/adapter/model identities, immutable scenario and artifact
hashes, prompts/schema versions, tool permissions, complete redacted trajectories,
budget reservations, approvals, route-validation reports, metric definitions,
analysis scripts and environment specifications. Provider fingerprints are not
verified model-weight hashes. Preserve enough access-controlled inputs for replay;
hash-only logs cannot reproduce missing artifacts. Never retain API keys or raw
credential-bearing requests.

Required review decisions include: scientific meaning of hazard products;
authoritative source classes; freshness/coverage/vehicle rules; coordinate/edge
identity treatment; human approval roles; profile applicability and preference
scales; latency/call limits; retention/privacy; human-study requirements; and
any future service use or spending. None is marked approved here.

Reconsider the architecture if adaptive rules match or exceed LLM outcomes at
lower cost, if the simulator's useful next choices are still largely predetermined,
if source provenance cannot support C4 contracts, or if ranking instability makes
a single recommendation misleading. Possible responses are a rules default,
improved observation/task design, synthetic-only scope, or explicit human-facing
trade-off presentation. Report these limitations rather than add agents or
weaken verification to obtain favorable results.

Literature motivates the protocol; it does not predict its results. The evidence
review and ADRs identify the source scope for each design assumption. Synthetic
experiments, even with real model calls, are not field validation of emergency
logistics.
