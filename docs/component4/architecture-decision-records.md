# Component 4: Architecture Decision Records

Date: 2026-10-21. Design baseline: `feature/component4-agentic-ai`,
`94b148ce8ebb5745d3abf52bc134bff5d614ea9b`.

Read with the [evidence review](research-evidence-review.md),
[blueprint](adaptive-agent-blueprint.md) and [roadmap](implementation-evaluation-roadmap.md).
All proposed choices are subject to supervisor/domain-owner review. This document
does not grant that approval or change application behavior.

## Status semantics

| Status | Meaning |
| --- | --- |
| PROPOSED | A recommendation awaiting review and authorization. |
| APPROVED | An authorized person explicitly accepted a recorded decision; no new decision has this status here. |
| IMPLEMENTED | Code exists; this says nothing about field validity. |
| VALIDATED | Evidence supports a precisely stated scope, such as mocked contract tests, not universal correctness. |
| REJECTED | Excluded from a stated scope for recorded reasons; reconsideration is possible. |

Alternative choices described as deferred have not been universally rejected.
Existing safety invariants are requirements carried forward, not newly approved
research policy. All ten recommended changes below have status **PROPOSED**.

## Actual baseline and compatibility

Inspected source: `src/agentic_orchestration/{contracts,validation,ranking,
execution,orchestration,agent_controller,llm_planner,groq_client,ollama_client}.py`;
the seven `tests/component4/test_*.py` modules; and the Component 4 README.
The automated suite reports 194 tests passing. Prior session smoke observations
recorded three successful Groq action calls; they were not rerun in this milestone
and no durable smoke-test artifact is claimed.

| Existing capability | Status and bounded evidence |
| --- | --- |
| `c4.planner-action/1.0.0` strict decoder | IMPLEMENTED; VALIDATED against malformed/extra-field cases in mocked tests. |
| Controller state/allowlist and repeated-kind rejection | IMPLEMENTED; VALIDATED on synthetic fixtures. |
| Evidence verification and deterministic ranking | IMPLEMENTED; VALIDATED against synthetic independent authorities, not real artifacts. |
| Groq transport for `openai/gpt-oss-20b` | IMPLEMENTED; mocked transport tests and basic live connectivity observed. |
| Ollama transport | IMPLEMENTED and mocked; installed local model is not evidence of tested inference quality. |
| Rich feedback, refresh actions, persistence, resume, policy registry | PROPOSED; not implemented in the baseline. |

The new action/state contracts require separate versions; the current strict
decoder intentionally rejects proposed new tool fields. Existing v1 callers and
tests must remain supported until an explicit migration decision.

## Comparison matrix

This is an engineering comparison, not a measured performance ranking.

| Dimension | Candidate | Benefit | Cost or risk | Initial position |
| --- | --- | --- | --- | --- |
| Planning | Fixed sequence | Simple, reproducible baseline | Repeats work and cannot choose useful recovery | Retain comparator A |
| Planning | Adaptive rules | Explicit, testable conditions | Rule maintenance and combination coverage | Build comparator B first |
| Planning | Feedback-driven LLM | Flexible interpretation of bounded state | Variability, cost, incorrect choices | Proposed comparator C |
| Planning | Symbolic/HTN | Explicit dependencies and plan validation | Domain encoding and integration overhead | Defer full planner |
| Agents | Collaborating specialists | Potential task separation | Coordination, duplicate work, wider audit surface | Defer |
| Implementation | Minimal typed controller | Direct fit with existing contracts | Persistence/recovery must be built carefully | Proposed host core |
| Implementation | Established framework | Existing graph/checkpoint facilities | Dependency and semantics migration | Evaluate only if justified |
| Integration | Direct function calls | Low overhead | Tight coupling and mutation/import side effects | Behind adapters only |
| Integration | Typed adapters | Stable evidence and failure boundary | Mapping/validation work | Proposed default |
| Integration | HTTP services | Independent deployment | Availability, timeouts, authentication | Use where actually available |
| Integration | Synthetic tools | Controlled failure and known ground truth | Limited real-world validity | Proposed first environment |
| Preferences | Expert profiles | Traceable decision authority | Requires elicitation and approval | Proposed first policy source |
| Preferences | AHP/BWM | Structured expert elicitation | Consistency, scale interpretation, judgment burden | Offline pilot |
| Preferences | Entropy weighting | Uses candidate dispersion | Changes with pool; not a statement of mission values | Diagnostic comparator only |
| Ranking | Constrained weighted sum | Existing transparent baseline | Compensation and normalization sensitivity | Retain initially |
| Ranking | TOPSIS | Alternative trade-off geometry | Extra distance/normalization assumptions | Offline comparator |
| Ranking | Pareto filtering | Reveals dominated trade-offs | Does not select a final route; incomplete candidates | Diagnostic first |
| Ranking | Robust/sensitivity analysis | Reveals unstable recommendations | Needs justified uncertainty ranges | Proposed analysis layer |
| Recovery | Immediate termination | Simple fail-closed behavior | Misses recoverable cases | Mandatory fallback |
| Recovery | Deterministic fallback | Predictable approved recovery | Can become rigid or silently hide changes | Explicit, audited option |
| Recovery | Bounded replanning + human review | Useful recovery without unlimited autonomy | State, budget and approval complexity | Proposed extension |

## ADR-01: Single agent versus multiple agents

**Status: PROPOSED.** Problem: coordinate hazard, network and routing requests
without confusing component ownership with a need for several LLMs. Requirements:
one mission authority, one shared budget, reproducible decisions and a small tool
set. Candidates are one planner, role-separated cooperating agents, and a
hierarchical supervisor with specialist agents.

Recommend one logical planner using typed component tools. Component specialists
remain ordinary verified services/adapters, not additional autonomous models.
Advantages are a single audit trajectory, simpler budgets and fewer coordination
failure paths. The disadvantage is no demonstrated parallel-specialist benefit.
Multi-agent design is deferred because no local experiment establishes that its
coordination cost is justified. Stateful benchmark work [R02](research-evidence-review.md#r02-toolsandbox)
and [R04](research-evidence-review.md#r04-tps-bench) motivates testing tool behavior;
neither proves single-agent superiority. This recommendation is engineering judgment.

Failure/fallback: if the planner is unavailable or repeatedly chooses ineligible
tools, use an explicitly selected rule planner or abstain; never launch hidden
specialists. Validate by comparing completion, cost and audit complexity if a
multi-agent prototype is later proposed. Reconsider when independent subtasks,
distinct expertise and measured gains outweigh coordination and privacy costs.
Supervisor review is required before expanding autonomy or experimental scope.

## ADR-02: Planning method

**Status: PROPOSED.** Problem: adapt to evidence availability and typed failures.
Assume source tools expose honest, independently checked state. Compare the fixed
workflow, deterministic rules, ReAct-inspired one-step feedback planning and full
symbolic/HTN planning. Recommend a feedback-driven planner with a host-maintained
dependency model and explicit preconditions, plus a deterministic adaptive baseline.

The LLM proposes one eligible operation, then receives validated outcome feedback.
A plan summary may express bounded subgoals, but never grants execution rights.
No chain-of-thought transcript is needed or treated as evidence. Benefits are
incremental decisions and observable revisions; limitations are stochastic choice
and dependence on observation quality. A full symbolic planner is deferred until
dependency branching exceeds what a small typed registry can express. A fixed
workflow remains necessary as an experimental control, not the proposed adaptive
solution. [R01](research-evidence-review.md#r01-react) and
[R12](research-evidence-review.md#r12-planbench-additional-foundational-comparison)
inform this comparison, without guaranteeing this model's planning ability.

Failure/fallback: typed rejection feedback is allowed within budgets; unchanged
repeated invalid proposals end the run. Validate on unseen combinations of stale,
missing, failed and reusable dependencies. Reconsider LLM use if rules match its
resolution quality with lower cost, or use symbolic planning if requirements need
formal plan guarantees. No superiority claim precedes the held-out comparison.

## ADR-03: Controller and framework

**Status: PROPOSED.** Problem: execute decisions with persistence, limits and
consistent permissions. Compare extending the minimal custom controller, migrating
to an agent framework, and a hybrid where a framework schedules host-owned tools.
Recommend retaining the typed host as the authority, initially without a framework.

The existing source already supplies strict decoding, validation and budgets.
Benefits are continuity and controlled change; disadvantage is engineering work
for crash recovery and resume. [LangGraph documentation](https://docs.langchain.com/oss/python/langgraph/overview)
is evidence of a framework option, not evidence that its defaults match these
contracts or provide operational safety. A future framework may manage scheduling
and checkpoints only behind equivalent host gates. Framework adoption and genuine
adaptation are separate questions.

Failure/fallback: incomplete persistence transactions fail closed; uncertain tool
completion is reconciled by idempotency identity, not blind replay. Validate state
transitions, crash points and budget conservation. Reconsider a framework if an
equivalence prototype demonstrably reduces implementation/maintenance effort
without hidden retries, model calls or altered permissions. Scope is a local
research process, not a distributed production guarantee.

## ADR-04: Model provider and deployment

**Status: PROPOSED.** Problem: obtain reliable bounded decisions under hardware,
privacy and service constraints. Compare current Groq, local Ollama, another
hosted provider and deterministic operation without an LLM. Recommend Groq as the
initial *experimental* model transport because it is implemented and basic
connectivity has been observed. This is not evidence of superior reasoning,
accuracy, latency distribution or reliability.

Groq sends approved synthetic observations to a remote service; account quotas,
network access and provider changes constrain repeatability. Local Ollama offers
deployment control but the 8 GB laptop has shown memory pressure and local model
inference has not been evaluated. Other providers add comparative options but
have no implemented client here. Official [Groq](https://console.groq.com/docs/model/openai/gpt-oss-20b)
and [Ollama](https://docs.ollama.com/capabilities/structured-outputs) documentation
supports interface possibilities only. [R03](research-evidence-review.md#r03-tau-bench)
motivates repeated-run reliability assessment.

Failure/fallback: authentication, quota and rate-limit failures stop that provider
session without automatic retries/upgrades. An approved deterministic planner may
be started explicitly with retained budgets; no silent provider switch or local
model download. Validate all providers on the same frozen observation/tool suite,
record revisions when supplied and label unknown revisions honestly. Reconsider
after measured quality, privacy, cost and hardware comparisons. Provider use,
external data disclosure and spending require appropriate authorization.

## ADR-05: Component tools and dependency orchestration

**Status: PROPOSED.** Problem: useful component operations currently expose
different interfaces and incomplete evidence. Compare direct imports, typed
adapters, HTTP and simulation. Recommend a typed adapter registry with synthetic
tools first and approved real bindings later. Transport choice belongs inside
each adapter; it does not determine tool semantics.

Requirements: opaque allowlisted artifact references, validated inputs/outputs,
versioned capabilities and explicit unsupported operations. Benefits are stable
failure semantics and independently testable evidence. Costs are mapping work and
the possibility that upstream limitations block integration. Direct scripts are
not offered to the LLM: some write files, use fixed external paths or have import
side effects. Component 2's existing GET API audits artifacts; it is not a graph
refresh service. Component 1/3 functions are not deployed APIs.

Support: repository inventory in the [blueprint](adaptive-agent-blueprint.md#baseline-integration-inventory)
and the stateful evaluation rationale of [R02](research-evidence-review.md#r02-toolsandbox).
Fallback: unavailable artifacts remain unavailable; never substitute synthetic
data into a real mission silently. Validate each adapter against saved fixtures,
negative evidence and contract mapping. Reconsider HTTP-only deployment if owners
provide independently maintained services with equivalent provenance. Real owner
interfaces, data validity and repair permissions require agreement.

## ADR-06: Mission preferences and weight derivation

**Status: PROPOSED.** Problem: make mission-specific trade-offs accountable.
Assume hard eligibility remains independent of preferences. Compare expert
profiles, AHP, BWM, entropy weights and expert/uncertainty hybrids. Recommend
versioned expert-approved profiles with offline elicitation and sensitivity
analysis; the LLM may propose an existing eligible profile ID for host confirmation,
never numeric weights or approval.

With only time and exposure, directly elicit and validate a trade-off ratio under
the chosen criterion scales. For three or more independently justified criteria,
pilot AHP and BWM with the same experts and consistency checks. Do not add criteria
merely to justify a more elaborate method. An elicited ratio or pairwise score
does not automatically constitute a valid cardinal utility scale.

Evidence: [R08](research-evidence-review.md#r08-best-worst-method),
[R13](research-evidence-review.md#r13-ahp-additional-foundational-comparison),
and [R09](research-evidence-review.md#r09-entropy-and-normalization). Engineering
reason: entropy weights reflect the alternative matrix, not emergency priorities;
use entropy diagnostically. BWM's lower comparison burden is less useful for two
criteria. AHP hierarchies and fuzzy/Bayesian hybrids add elicitation/modeling
assumptions and are deferred until expert uncertainty warrants them.

Failure/fallback: unknown mission/profile or inconsistent judgments require human
review; no inferred weights or silent default. Validate agreement, stability,
elicitation effort and route sensitivity; do not label agreement as correctness.
Reconsider if expert evidence favors a different elicitation procedure or weight
intervals over a single profile. Experts/supervisor must approve criterion meaning,
profiles, validity and escalation thresholds; no profiles are approved here.

## ADR-07: Route ranking and sensitivity

**Status: PROPOSED** retention of the existing constrained weighted sum as the
initial final gate; that algorithm itself is already IMPLEMENTED. Problem:
compare eligible alternatives transparently without confounding planner evaluation.
Candidates are weighted sum, TOPSIS, Pareto filtering and robust decision support.
Requirements are verified metrics, consistent units and no trade-off against
violated hard limits.

Recommend freezing current ranking for the planner comparison, with separate
offline TOPSIS/Pareto/weight-normalization sensitivity experiments. Advantages are
continuity and interpretable contributions; limitations include candidate-relative
normalization, compensation within eligibility and zero-weight tie behavior.
TOPSIS adds an ideal-distance interpretation, not established accuracy. Pareto
filtering identifies trade-offs but does not choose a mission recommendation.
Do not insert it into the current gate without analyzing zero-weight and tie
semantics. Robust analysis should initially report instability, not silently
replace the selected route with a new optimizer.

Evidence: [R09](research-evidence-review.md#r09-entropy-and-normalization),
[R14](research-evidence-review.md#r14-topsis-comparison-additional-foundational-comparison)
and [R07](research-evidence-review.md#r07-temporal-multiobjective-routing).
Failure/fallback: missing authoritative metrics or changed bindings abstain.
Validate synthetic exact optima on small enumerated graphs, threshold boundaries,
pool perturbations and weight intervals. Reconsider if domain utility evidence
or instability demonstrates that the current aggregation is inappropriate.
No candidate-pool method proves graph-wide optimality or calibrated risk.

## ADR-08: Provenance, uncertainty and spatial evidence

**Status: PROPOSED.** Problem: prevent plausible numbers or timestamps from
becoming unsupported authority. Compare permissive metadata acceptance, verified
immutable artifact bindings, and full probabilistic uncertainty modeling.
Recommend immutable manifests and independent artifact/metric verification first,
with explicit unknown, stale, partial and contradictory states.

Requirements include true source observation time where applicable, build time
kept separate, CRS alignment, preserved `(u,v,key)` identities, geometry/coverage
evidence and vehicle compatibility. Benefits are inspectability and invalidation;
costs include manifest storage and refusal when upstream information is absent.
A checksum proves byte identity, not truthful data or valid hazard science.
Current C4 interfaces are foundations, not implemented source-artifact verifiers.

Evidence: [R06](research-evidence-review.md#r06-road-network-resilience-review)
and the conditional algorithmic scope in [R07](research-evidence-review.md#r07-temporal-multiobjective-routing).
Probabilistic propagation is deferred until validated models support distributions;
synthetic susceptibility cannot be converted into observed event probability.
Failure/fallback: reject unknown-as-zero conversions and contradictory sources;
refresh only through supported approved sources, otherwise abstain. Validate
NoData, CRS, edge-key, stale-manifest and tamper cases. Reconsider uncertainty
models after calibration studies and authoritative source availability. Domain
owners must approve freshness/coverage semantics and acceptable source classes.

## ADR-09: Recovery, clarification and human oversight

**Status: PROPOSED.** Problem: distinguish recoverable failure from an obligation
to stop. Compare immediate termination, approved fallback, bounded replanning and
human review. Recommend a typed failure taxonomy plus bounded replanning for
recoverable cases, with terminal abstention and human approval as first-class
outcomes. Automatic retries remain transport-disabled; a retry is a visible
host-authorized tool action that consumes the same mission budget.

Benefits are recoverable completion and visible escalation; disadvantages are
more state transitions and dependence on honest error classification. The host,
not text returned by a tool, grants retryability and approval. Unknown operations,
invalid evidence and hard-limit violations cannot be fixed by weakening policy.
Approval permits specified operations, never acceptance of invalid evidence.

Evidence: [R05](research-evidence-review.md#r05-toolmaze) motivates failure cases;
[R10](research-evidence-review.md#r10-nist-ai-rmf) informs oversight structure.
Failure/fallback: terminal authentication/security errors, exhausted budgets and
unresolved contradictions abstain. Clarification suspends with a scoped token;
validated human replies create a new revision without resetting counters.
Validate no-progress loops, expired approvals, crash/resume, transient/permanent
classification and human-denied paths. Reconsider autonomy only after recoveries
are measurable without more prohibited transitions. Approval owners and deadlines
must be agreed before enabling real mutating operations.

## ADR-10: Logging, privacy and auditability

**Status: PROPOSED.** Problem: retain evidence of decisions without leaking
credentials, sensitive locations or untrusted content. Compare raw prompt/tool
logging, minimal outcome-only logging and structured allowlisted event records.
Recommend structured events plus separately access-controlled immutable artifacts.

Record request/response hashes, schema/tool/model identities, evidence refs,
permissions, timing, usage, rejection codes and policy/snapshot revisions. Keep
secrets, HTTP authorization headers and raw provider error bodies out of logs.
Do not store hidden model reasoning. Current `ModelCallRecord` includes bounded
request observations; deployments need redacted export/access policies rather
than a false claim that current traces contain no mission labels.

Support: [R11](research-evidence-review.md#r11-agentdojo-additional) motivates
untrusted-output threats; [R10](research-evidence-review.md#r10-nist-ai-rmf)
supports accountability. Benefits are replayability and data minimization; costs
are artifact governance and reduced visibility when only hashes are retained.
Raw logging is rejected for credential-bearing requests. Outcome-only logging
is insufficient for independent verification.

Failure/fallback: logging/export failures block release where the audit record
is required; redact or quarantine unsafe text, not silently truncate numeric
evidence into a different claim. Validate sentinel-secret leakage, malicious
metadata and replay version mismatch. Reconsider retention fields after an
approved research-data policy. Retention duration, access, remote disclosure and
human-study data collection require supervisor/institutional decisions.

## Review and reconsideration gates

Approval is required for domain policy values, source validity semantics,
recalculation/repair permissions, external disclosure and experiment spending.
The LLM architecture remains conditional on the A/B/C comparison. If adaptive
rules achieve equivalent resolution with lower cost, report that result and use
rules as the practical default; an unfavorable LLM result is still a valid
dissertation finding.
