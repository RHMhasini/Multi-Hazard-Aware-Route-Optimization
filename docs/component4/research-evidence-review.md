# Component 4: Research Evidence Review

Date: 2026-10-21 (Asia/Colombo). Status: **PROPOSED** research interpretation for review.

Companion documents: [architecture decisions](architecture-decision-records.md),
[technical blueprint](adaptive-agent-blueprint.md), and
[implementation and evaluation roadmap](implementation-evaluation-roadmap.md).

## 1. Scope and research questions

The project concerns multi-hazard-aware emergency logistics routing in
Sabaragamuwa Province. This review informs an adaptive orchestration prototype;
it does not validate emergency operations or the scientific accuracy of upstream
hazard models. The intended contribution is evidence-driven selection and
revision of component operations while deterministic host code retains decision
constraints and verification.

The research questions are:

1. RQ1: Does a feedback-driven planner improve correct dependency selection and
   mission resolution over a fixed workflow under missing or changing evidence?
2. RQ2: Does an LLM planner add value beyond an adaptive deterministic planner
   using the same observations, tools and permissions?
3. RQ3: Can recovery improve completion without increasing unauthorized actions,
   unsupported recommendations or unnecessary tool calls?
4. RQ4: How sensitive are eligible-route recommendations to approved preferences,
   normalization and uncertainty in evidence?
5. RQ5: Which claims survive repeated runs and held-out scenarios, and which
   remain limited to synthetic demonstrations?

## 2. Search and verification method

This is a **selective critical evidence review**, not a formal systematic review.
Searches and page checks were performed on 2026-10-21. Publication priority was
2024-2026, with older foundational planning and decision-analysis methods retained.
The ten user-supplied references were checked first. Additional searches addressed
prompt injection, symbolic planning, AHP, TOPSIS and controller frameworks.

Example reproducible queries included exact DOI strings; `ToolSandbox stateful
tool use`; `AgentDojo NeurIPS 2024`; `PlanBench NeurIPS`; and exact titles for
the road-resilience and normalization articles. Sources were arXiv, ACL Anthology,
ICLR/NeurIPS proceedings, publisher-indexed pages, NIST, and an author institution's
publication record. Generic model rankings and promotional summaries were excluded
as evidence of task-specific capability. No exhaustive database export, duplicate
screening protocol, PRISMA process or quality-score meta-analysis was conducted.

Inclusion required a verifiable title, year and source and relevance to stateful
tools, recovery, constrained decision support or routing evidence. A preprint was
not excluded solely for being a preprint, but receives less evidential weight.
Unverified performance claims, vendor comparisons without a shared task protocol,
and papers inferred merely from plausible titles were excluded.

Access categories:

- **F**: relevant full-text material was accessible and consulted.
- **A**: conclusions are limited to the official abstract/metadata.
- **I**: publisher-indexed abstract or relevant excerpts were accessible, while
  direct full-text retrieval was blocked. This is not a complete full-text review.
- **D**: official technical/governance documentation, not an empirical paper.

Some ScienceDirect pages returned 403; MDPI returned 429; the ToolSandbox
conference PDF exceeded the browsing tool's size limit. Accessible author arXiv
HTML was used where available. These access failures are not evidence against
the publications. No paywall, CAPTCHA or access control was bypassed.

## 3. Verified evidence register

The findings below are qualitative paraphrases. They are findings of the cited
studies, not results obtained by this project. Paper-specific model scores are
deliberately not transferred to the Groq model or emergency-routing domain.

### R01. ReAct

Shunyu Yao et al. **ReAct: Synergizing Reasoning and Acting in Language Models**.
ICLR 2023; first arXiv submission 2022. Peer-reviewed conference paper. Access F,
including [author full text](https://arxiv.org/html/2210.03629v3);
[record](https://arxiv.org/abs/2210.03629).

Method: interleave model reasoning and external actions on question answering and
interactive task benchmarks. Reported finding: interacting with external evidence
improved several studied task outcomes relative to the paper's baselines.
Limitations: those environments and models differ from emergency routing, and
successful prompting is not a safety guarantee. Application: motivate a
feedback loop, not a requirement to expose private chain-of-thought or allow
the LLM to generate authoritative measurements.

### R02. ToolSandbox

Jiarui Lu et al. **ToolSandbox: A Stateful, Conversational, Interactive Evaluation
Benchmark for LLM Tool Use Capabilities**. Findings of NAACL 2025, pp. 1160-1183.
Peer-reviewed conference paper. DOI
[10.18653/v1/2025.findings-naacl.65](https://aclanthology.org/2025.findings-naacl.65/).
Access F for [2024 author version](https://arxiv.org/html/2408.04682v1), A for
the conference record; the versions must not be treated as line-identical.

Method: executable stateful tools, user simulation and intermediate/final
milestones. Reported finding: state dependencies and insufficient information
remain challenging; simulator errors and manually authored evaluations are
limitations. Application: evaluate valid state transitions and clarification
with independently authored scenario oracles, rather than judge JSON alone.

### R03. tau-bench

Shunyu Yao, Noah Shinn, Pedram Razavi and Karthik Narasimhan.
**tau-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains**.
ICLR 2025, peer-reviewed conference paper. Access F.
[Proceedings](https://proceedings.iclr.cc/paper_files/paper/2025/hash/1b126cc38b8638e07bef37e7b2bb72bf-Abstract-Conference.html)
and [paper](https://proceedings.iclr.cc/paper_files/paper/2025/file/1b126cc38b8638e07bef37e7b2bb72bf-Paper-Conference.pdf).

Method: policy-constrained retail/airline tool-user interactions and repeated
trials. Reported finding: task success and consistency are distinct weaknesses.
Its pass-hat-k statistic measures repeated success; final-state rewards can miss
intermediate policy violations. Application: separately score outcome, prohibited
transitions and repeatability. Its simulated customer domains do not establish
logistics performance.

### R04. TPS-Bench

Hanwen Xu, Xuyao Huang, Yuzhe Liu and Zhijie Deng. **TPS-Bench: Evaluating AI
Agents' Tool Planning & Scheduling Abilities in Compounding Tasks**. ACL 2026,
long papers, pp. 34949-34961. Peer-reviewed conference paper. Access A.
[10.18653/v1/2026.acl-long.1614](https://aclanthology.org/2026.acl-long.1614/).

Method: compound multi-tool tasks scored for completion and scheduling efficiency.
The abstract reports a trade-off between successful completion and execution
time across evaluated agents, plus an initial training study. Application:
measure tool economy and latency alongside completion. This does not establish
that parallel execution, reinforcement learning or hundreds of tools are needed
for Component 4; detailed protocol claims require full-paper review.

### R05. ToolMaze

Dongsheng Zhu et al. **When Tools Fail: Benchmarking Dynamic Replanning and
Anomaly Recovery in LLM Agents**. arXiv:2606.05806v1, submitted 4 June 2026.
**Preprint; no peer-reviewed venue verified.** Access F.
[Record](https://arxiv.org/abs/2606.05806),
[full text](https://arxiv.org/html/2606.05806v1).

Method: dependency-graph tasks with explicit/implicit and transient/permanent
perturbations. Reported finding: semantic corruption and recovery expose
weaknesses hidden by ordinary completion tests. Controlled synthetic topology
limits external validity. Application: inject classified failures at predefined
tool events and score alternate valid paths; independent numeric verification
must still detect contradictions that an LLM overlooks.

### R06. Road-network resilience review

**Resilience of road networks to natural hazards: A systematic literature review**.
Transportation Engineering 23 (2026), 100420. Journal review article.
Access I, limited to the publisher-indexed abstract/highlights; complete review
methods were not independently audited.
[DOI](https://doi.org/10.1016/j.treng.2026.100420),
[publisher](https://www.sciencedirect.com/science/article/pii/S2666691X26000011).

Method: synthesis of road-resilience studies, distinguishing topological,
functional and hybrid measures. Reported gaps include link-level assessment,
temporal behavior and multi-hazard interactions. Application: test changing
network and evidence conditions, not merely static route selection. The review
does not validate an LLM planner or imply that this project's susceptibility
scores are calibrated probabilities.

### R07. Temporal multiobjective routing

Cristina Bazgan, Johannes Kager, Clemens Thielen and Daniel Vanderpooten.
**A general label setting algorithm and tractability analysis for the
multiobjective temporal shortest path problem**. Networks 85(1), 76-90 (2025);
first online 2 October 2024. Peer-reviewed journal article. Access I for relevant
publisher text; institutional abstract also checked.
[DOI](https://doi.org/10.1002/net.22253),
[publisher](https://onlinelibrary.wiley.com/doi/full/10.1002/net.22253),
[institutional record](https://portal.fis.tum.de/en/publications/a-general-label-setting-algorithm-and-tractability-analysis-for-t/).

Method: label-setting algorithm and complexity analysis under monotone objectives.
Reported result: tractability depends on conditions including the nondominated
output size. Application: distinguish static snapshot replanning from temporal
shortest-path optimization. Component 3's bounded labels/sweeps are not certified
implementations of this theorem or proof of complete Pareto enumeration.

### R08. Best-Worst Method

Jafar Rezaei. **Best-worst multi-criteria decision-making method**. Omega 53,
49-57 (June 2015; DOI contains 2014). Peer-reviewed journal article. Access I/A.
[DOI](https://doi.org/10.1016/j.omega.2014.11.009),
[publisher](https://www.sciencedirect.com/science/article/pii/S0305048314001480).

Method: elicit best/other and other/worst comparisons and derive criteria
weights with consistency assessment. The paper reports a favorable comparison
with AHP in its study; this is not universal superiority. Application: possible
offline expert elicitation. With only two criteria, its comparison-saving
argument is weak. The later linear BWM formulation must not be attributed to
the 2015 paper without checking the separate formulation.

### R09. Entropy and normalization

Ewa Roszkowska. **Entropy and Normalization in MCDA: A Data-Driven Perspective on
Ranking Stability**. Entropy 28(1), 114 (2026). Peer-reviewed journal article.
Access I for indexed publisher/archived article sections; direct retrieval was
blocked. [DOI](https://doi.org/10.3390/e28010114),
[publisher](https://www.mdpi.com/1099-4300/28/1/114),
[archived article](https://pmc.ncbi.nlm.nih.gov/articles/PMC12839561/).

Method: normalization comparisons and controlled sensitivity analysis using
TOPSIS. Reported finding: normalization and criterion distributions influence
ranking stability. Entropy is used as a diagnostic descriptor, **not a weighting
mechanism in this study**. Application: vary normalization and candidate pools;
do not equate data dispersion, stable ranks or entropy weights with ethically
appropriate emergency preferences.

### R10. NIST AI RMF

NIST. **AI Risk Management Framework 1.0**, Core (2023). Government voluntary
framework, not a peer-reviewed performance experiment. Access D.
[Core](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/).
The official page notes a revision is in progress; this review refers to 1.0.

Method: governance guidance organized around Govern, Map, Measure and Manage.
It provides no benchmark finding or certification for this project. Application:
identify risk owners, record limitations and evaluate failure behavior. The
proposed approval and audit processes are engineering choices, not a claim of
NIST compliance or official approval.

### R11. AgentDojo (additional)

Edoardo Debenedetti et al. **AgentDojo: A Dynamic Environment to Evaluate Prompt
Injection Attacks and Defenses for LLM Agents**. NeurIPS 2024 Datasets and
Benchmarks Track, peer-reviewed. Access F for the paper's abstract/introduction,
not a complete defense replication.
[Proceedings](https://proceedings.neurips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html).

Method: adversarial instructions placed in tool-returned data. Reported finding:
both utility failures and successful attacks occur in tested agents. Application:
enforce permissions outside model text and include malicious tool-output cases.
Its tasks do not prove that our specific schemas eliminate injection risk.

### R12. PlanBench (additional foundational comparison)

Karthik Valmeekam et al. **PlanBench: An Extensible Benchmark for Evaluating Large
Language Models on Planning and Reasoning about Change**. NeurIPS 2023 Datasets
and Benchmarks Track, peer-reviewed. Access A.
[Proceedings](https://proceedings.neurips.cc/paper_files/paper/2023/hash/7a92bcdede88c7afd108072faf5485c8-Abstract-Datasets_and_Benchmarks.html).

Method: formal planning-domain tasks. Reported finding: evaluated LLMs fall short
on several planning capabilities. Application: a valid-looking action is not
proof of planning competence; formal preconditions and a rule-based comparator
are needed. Findings about earlier models are not an estimate for the current model.

### R13. AHP (additional foundational comparison)

Thomas L. Saaty. **How to make a decision: The analytic hierarchy process**.
European Journal of Operational Research 48(1), 9-26 (1990). Peer-reviewed
methodological article. Access A.
[DOI](https://doi.org/10.1016/0377-2217(90)90057-I),
[publisher](https://www.sciencedirect.com/science/article/pii/037722179090057I).

Method: hierarchical preference modelling with pairwise judgments. The abstract
introduces the framework rather than a routing accuracy experiment. Application:
an elicitation candidate when stakeholders can assess criterion trade-offs.
Domain judgment and consistency checks remain necessary; a hierarchy does not
make subjective priorities objective facts.

### R14. TOPSIS comparison (additional foundational comparison)

Serafim Opricovic and Gwo-Hshiung Tzeng. **Compromise solution by MCDM methods:
A comparative analysis of VIKOR and TOPSIS**. European Journal of Operational
Research 156(2), 445-455 (2004). Peer-reviewed article. Access I/A.
[DOI](https://doi.org/10.1016/S0377-2217(03)00020-1),
[publisher](https://www.sciencedirect.com/science/article/pii/S0377221703000201).

Method: compare ideal-distance aggregation and normalization using a numerical
example. Reported finding: the methods can produce similar and different
compromises. Application: TOPSIS is a meaningful ranking comparator, not an
intrinsically safer or more accurate replacement for constrained weighted sum.

### Technical sources T01-T03

These establish documented interfaces only, not comparative scientific evidence:

- T01: [Groq GPT-OSS model documentation](https://console.groq.com/docs/model/openai/gpt-oss-20b).
- T02: [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs).
- T03: [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview).

Checked 2026-10-21. Dependency versions, provider availability and account limits
must be recorded again for a future experiment. No paid/model API was called.

## 4. Critical synthesis and research gap

Published work motivates stateful evaluation, recovery tests and external
verification. It does **not** determine which architecture will perform best on
this project's tools, prove an LLM will beat rules, or validate local hazard data.
Combining these ideas in this application is a design opportunity, not yet a
demonstrated novelty claim. A wider database search and supervisor review are
needed before claiming that no prior comparable emergency-routing system exists.

The present research gap is therefore operationalized narrowly: whether a
host-constrained adaptive planner can select useful dependency operations and
recover under independently specified changes, and whether an LLM adds measurable
benefit beyond deterministic adaptation. Route mathematics is held constant to
avoid attributing a ranking change to agent intelligence.

## 5. Evidence labels and open claims

| Label | Meaning | Example |
| --- | --- | --- |
| Published evidence | A bounded claim supported by a cited source | Stateful tool benchmarks expose failures missed by single actions. |
| Repository observation | Direct source/test inspection at the pinned commit | Evaluation outcomes currently terminate the controller. |
| Engineering judgment | A proposed design trade-off | Keep one typed controller before adding collaborating agents. |
| Assumption | A condition that must be checked | Component owners can provide immutable source artifacts. |
| Untested hypothesis | A falsifiable proposed benefit | LLM planning handles held-out feedback combinations better than rules. |

The following remain unvalidated: adaptive-planner superiority; external validity
of the simulator; expert agreement on mission preferences; calibration of hazard
scores; completeness of candidate routes; recovery under real services; and the
adequacy of freshness/coverage thresholds. Three successful Groq smoke actions
show basic connectivity and interface compatibility only. They are not a sample
large enough to estimate reasoning accuracy, reliability or route safety.

Decision status is defined in [the ADR document](architecture-decision-records.md#status-semantics).
All new recommendations require review; none is marked APPROVED.
