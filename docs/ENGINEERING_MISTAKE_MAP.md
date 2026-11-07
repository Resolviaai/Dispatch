# Vibe coding & software engineering: the mistake map

The core problem: you fix one bug, create three others, build unnecessary features, or ship something that technically works but solves the wrong problem.

These are the concepts behind those failures, with short definitions and prevention rules. This covers AI-assisted coding, web apps, mobile apps, backend systems, and software architecture.

---

## 1. Thinking and decision-making biases

| Concept                | One-line meaning                                        | How to prevent it                                      |
| ---------------------- | ------------------------------------------------------- | ------------------------------------------------------ |
| Solutionism            | Building a solution before confirming the problem.      | Define the user problem first.                         |
| Overengineering        | Making a simple problem unnecessarily complex.          | Choose the simplest design that meets requirements.    |
| Premature optimization | Optimizing before measuring a real bottleneck.          | Profile first; optimize second.                        |
| Scope creep            | The task keeps expanding beyond its original goal.      | Freeze scope and track new ideas separately.           |
| Feature creep          | Adding features that don't improve the core outcome.    | Require a clear user benefit for every feature.        |
| Gold plating           | Polishing beyond what the task requires.                | Define acceptance criteria before coding.              |
| Confirmation bias      | Looking only for evidence that your approach works.     | Actively test the strongest counterargument.           |
| Anchoring bias         | Getting stuck on the first solution or architecture.    | Compare at least one simpler alternative.              |
| Sunk-cost fallacy      | Keeping bad code because you've already invested time.  | Evaluate future cost, not past effort.                 |
| Planning fallacy       | Underestimating implementation and debugging time.      | Include integration, testing, and failure scenarios.   |
| Local optimization     | Improving one component while hurting the whole system. | Measure end-to-end outcomes.                           |
| Happy-path bias        | Testing only when everything goes right.                | Test invalid input, failures, retries, and edge cases. |

---

## 2. Coding and debugging mistakes

| Concept                   | One-line meaning                                            | How to prevent it                                    |
| ------------------------- | ----------------------------------------------------------- | ---------------------------------------------------- |
| Regression                | A new change breaks previously working functionality.       | Run regression tests after every change.             |
| Shotgun surgery           | One change requires edits across too many unrelated files.  | Improve module boundaries and encapsulation.         |
| Whack-a-mole debugging    | Fixing one symptom reveals another repeatedly.              | Find the underlying root cause first.                |
| Symptom patching          | Treating the visible error instead of its cause.            | Trace the failure to its origin.                     |
| Fix-induced bug           | A bug fix introduces a new bug.                             | Add a reproducing test before fixing.                |
| Side-effect explosion     | A change unexpectedly affects other components.             | Isolate side effects behind clear interfaces.        |
| Copy-paste programming    | Duplicated logic drifts out of sync.                        | Extract shared logic when duplication warrants it.   |
| Error swallowing          | Catching exceptions without handling them correctly.        | Log, propagate, or recover explicitly.               |
| Magic numbers             | Unexplained constants create fragile behavior.              | Name constants and document their meaning.           |
| Dead code                 | Unused code adds confusion and maintenance cost.            | Remove obsolete code safely.                         |
| Boolean blindness         | `true` or `false` arguments hide what a function means.     | Use descriptive names, enums, or objects.            |
| Defensive overcoding      | Excessive checks and fallbacks obscure real failures.       | Validate at system boundaries; handle known risks.   |
| Dependency confusion      | Assuming a library or package behaves as expected.          | Verify documentation, versions, and package sources. |
| Incomplete implementation | Stubs, fake data, or TODOs masquerade as finished features. | Test the actual end-to-end user flow.                |

---

## 3. Architecture and system-design failures

| Concept                      | One-line meaning                                                             | How to prevent it                                     |
| ---------------------------- | ---------------------------------------------------------------------------- | ----------------------------------------------------- |
| Big Ball of Mud              | The architecture becomes a tangled collection of code.                       | Establish clear module responsibilities.              |
| Tight coupling               | One component depends too heavily on another.                                | Use stable interfaces and dependency inversion.       |
| Low cohesion                 | A module handles unrelated responsibilities.                                 | Keep each module focused on one purpose.              |
| God object / God component   | One component controls too much of the system.                               | Split responsibilities by domain.                     |
| Premature abstraction        | Creating generic frameworks before real needs emerge.                        | Wait for repeated, proven patterns.                   |
| Abstraction leakage          | Internal implementation details spill across interfaces.                     | Hide internals behind deliberate contracts.           |
| Wrong abstraction            | A shared abstraction forces unrelated cases into one model.                  | Abstract only genuinely common behavior.              |
| Golden hammer                | Using one favorite technology for every problem.                             | Select tools based on requirements and trade-offs.    |
| Distributed monolith         | A supposedly separate service still requires coordinated changes everywhere. | Define service ownership and independent boundaries.  |
| Monolith splitting too early | Creating microservices before complexity justifies them.                     | Start modular; distribute only when needed.           |
| Architecture astronauting    | Designing elaborate systems for hypothetical future needs.                   | Optimize for current constraints and likely growth.   |
| Circular dependencies        | Modules depend on each other in a loop.                                      | Enforce a one-way dependency graph.                   |
| Shared mutable state         | Multiple components modify the same data unpredictably.                      | Define ownership and controlled state transitions.    |
| Single point of failure      | One component can take down the whole system.                                | Add redundancy where failure impact warrants it.      |
| Premature technology lock-in | Choosing tools that make future changes expensive.                           | Prefer reversible choices until evidence accumulates. |

---

## 4. Requirements and product failures

| Concept                            | One-line meaning                                                  | How to prevent it                                |
| ---------------------------------- | ----------------------------------------------------------------- | ------------------------------------------------ |
| Requirement ambiguity              | Instructions have multiple interpretations.                       | Define examples and expected behavior.           |
| Requirement omission               | A necessary behavior was never specified.                         | List functional and nonfunctional requirements.  |
| Premature coding                   | Implementation starts before the task is understood.              | Agree on a plan and acceptance criteria first.   |
| Overfitting to the prompt          | The app matches the request but misses the real user need.        | Validate with real user workflows.               |
| Happy-path product design          | The design ignores unusual but legitimate situations.             | Map empty, loading, error, and success states.   |
| Gold-plated UX                     | Fancy interfaces hide incomplete functionality.                   | Prioritize task completion over decoration.      |
| Premature generalization           | Supporting hypothetical users complicates the initial product.    | Build for the first validated use case.          |
| Missing nonfunctional requirements | Performance, accessibility, reliability, or privacy gets ignored. | Set measurable quality targets.                  |
| Incomplete task closure            | Work is declared done without verifying the outcome.              | Check every acceptance criterion before closing. |
| Solution drift                     | Iterations gradually move away from the original goal.            | Recheck the goal after each major change.        |

---

## 5. Data, API, and integration failures

| Concept              | One-line meaning                                                  | How to prevent it                                       |
| -------------------- | ----------------------------------------------------------------- | ------------------------------------------------------- |
| Schema drift         | Code and database structures stop matching.                       | Use versioned migrations and schema checks.             |
| Data inconsistency   | Different parts of the app show conflicting values.               | Define a source of truth.                               |
| Race condition       | Results depend on unpredictable execution timing.                 | Use synchronization and concurrency tests.              |
| Lost update          | Concurrent writes overwrite each other.                           | Use transactions or optimistic locking.                 |
| Stale cache          | Users see outdated data.                                          | Define cache invalidation rules and expiry.             |
| N+1 query problem    | One request triggers excessive database queries.                  | Batch or eagerly load related data.                     |
| Broken contract      | Frontend and backend disagree on request or response formats.     | Define and test API contracts.                          |
| Retry storm          | Repeated failures trigger excessive requests.                     | Use bounded retries, backoff, and jitter.               |
| Idempotency failure  | Repeating a request performs an action multiple times.            | Use idempotency keys for retryable operations.          |
| Time-zone bug        | Dates or times shift across locations or daylight-saving changes. | Store instants in UTC and model local zones explicitly. |
| Floating-point error | Decimal arithmetic produces inaccurate results.                   | Use decimal or integer arithmetic for money.            |
| Silent data loss     | Failures discard user data without warning.                       | Add transactional writes and recovery paths.            |

---

## 6. Security and privacy failures

| Concept                          | One-line meaning                                                | How to prevent it                                             |
| -------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------- |
| Broken access control            | Users can access other users' data or restricted actions.       | Enforce server-side authorization on every request.           |
| Authentication confusion         | Identity checks are incomplete or incorrectly implemented.      | Use established authentication libraries and secure sessions. |
| Secret leakage                   | API keys or passwords enter source code or logs.                | Use secret managers and scanning.                             |
| Injection                        | Untrusted input becomes executable commands or queries.         | Use parameterized queries and context-aware output handling.  |
| Excessive permissions            | Users or services have more access than needed.                 | Apply least privilege and deny by default.                    |
| Security misconfiguration        | Debug modes, public storage, or unsafe defaults expose systems. | Harden production settings and audit deployments.             |
| Dependency supply-chain attack   | A package or build tool introduces malicious code.              | Verify packages, pin versions, and scan dependencies.         |
| Missing rate limits              | Attackers can abuse login, APIs, or expensive operations.       | Apply per-user and per-IP limits where appropriate.           |
| Insecure file upload             | Uploaded files execute, leak data, or exhaust storage.          | Validate types, scan files, and isolate storage.              |
| Privacy overcollection           | The app stores more personal data than necessary.               | Minimize collection, retention, and access.                   |
| Insecure direct object reference | Changing a record ID exposes another user's resource.           | Verify resource ownership on the server.                      |
| Unsafe AI-generated code         | Generated code is trusted without adequate verification.        | Review, test, scan, and understand changes before shipping.   |

---

## 7. Testing, deployment, and maintenance failures

| Concept                     | One-line meaning                                                 | How to prevent it                                          |
| --------------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------- |
| Test gap                    | Important behavior has no automated coverage.                    | Test critical user journeys and failure cases.             |
| Mock overreliance           | Mocked tests pass while real integrations fail.                  | Add integration tests against real dependencies.           |
| Flaky tests                 | Tests pass or fail unpredictably.                                | Remove timing dependence and isolate test state.           |
| Test blindness              | Tests repeat the implementation's assumptions.                   | Test independent expected outcomes and edge cases.         |
| Environment drift           | Local, staging, and production behave differently.               | Standardize environments and configuration.                |
| Configuration drift         | Settings diverge between deployments.                            | Version and validate configuration.                        |
| Migration failure           | Database changes break existing data or deployments.             | Test migrations on realistic copies and plan rollback.     |
| Missing observability       | Production failures occur without useful diagnostic information. | Add structured logs, metrics, and tracing.                 |
| No rollback strategy        | A bad release cannot be safely reversed.                         | Use reversible deployments and tested recovery procedures. |
| Technical debt accumulation | Quick fixes make future changes increasingly expensive.          | Track debt and reserve time to repay high-impact items.    |
| Compatibility regression    | Changes break older clients, browsers, or APIs.                  | Define supported versions and run compatibility tests.     |
| Premature deployment        | A prototype is published without production safeguards.          | Use a release checklist and staged rollout.                |

---

## 8. AI-agent and vibe-coding-specific failures

| Concept                      | One-line meaning                                                        | How to prevent it                                                                           |
| ---------------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Context rot                  | The AI loses or misinterprets important project context.                | Maintain concise project rules and architecture notes.                                      |
| Patch stacking               | Repeated AI fixes pile up without resolving underlying design problems. | Stop, diagnose, and redesign when fixes keep accumulating.                                  |
| Unbounded agent autonomy     | An agent changes unrelated files or executes risky actions.             | Limit permissions, scope, and approval boundaries.                                          |
| Prompt drift                 | Successive prompts change requirements unintentionally.                 | Maintain one canonical specification.                                                       |
| AI sycophancy                | The AI agrees with a flawed approach instead of challenging it.         | Explicitly request counterarguments and failure analysis.                                   |
| Hallucinated dependencies    | The AI invents packages, APIs, or library features.                     | Verify against official documentation and registries.                                       |
| Plausible fake functionality | Buttons or workflows appear functional but do nothing meaningful.       | Test every feature from the user's perspective.                                             |
| Inconsistent conventions     | Generated code uses different patterns across the project.              | Define and enforce shared coding standards.                                                 |
| Blind acceptance             | Generated code is merged without understanding its effects.             | Review the diff and verify behavior before accepting it.                                    |
| Mega-patch risk              | A large AI-generated change hides defects across many files.            | Request small, reviewable, independently testable changes.                                  |
| False completion             | The AI claims success without running the required checks.              | Require actual test results and deployment verification.                                    |
| Agent instruction injection  | Untrusted repository content or documents manipulate agent behavior.    | Treat external content as data, restrict tools, and require approval for sensitive actions. |

---

## The Highest-Leverage Prevention System

Use this loop for every meaningful feature or bug fix:

1. **Specify**: Write the problem, scope, constraints, and acceptance criteria.
2. **Plan**: Identify affected modules, dependencies, risks, and the smallest safe change.
3. **Implement**: Make one focused change; avoid unrelated refactoring and feature additions.
4. **Verify**: Run relevant tests, inspect the diff, check security, and test regressions.
5. **Close**: Confirm every acceptance criterion; report remaining risks and unfinished work honestly.

**Measurement**: Track regression rate, escaped bugs, rework time, and acceptance criteria completed.

**Trigger rule**: If two consecutive fixes fail or create new bugs, stop patching. Reproduce the failure, inspect the architecture, and find the root cause before making another change.
