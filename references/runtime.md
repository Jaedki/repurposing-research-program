# Deterministic Serial Runtime

Initialize with one or more human fields:

```text
py -3 scripts/orchestrate_program.py init <run_folder> [--human-gene GENE] [--human-disease DISEASE] [--human-phenotype PHENOTYPE]
```

Drive the run only through `orchestrate_program.py`. Obey `next -> start -> validate-result -> complete`. The controller permits one active job, immutable hashed packets, staged results, deterministic dependencies, retries, bounded checkpoints, and resume after interruption. Validation and commit hashes make replay idempotent; `next` resumes a validated or interrupted commit automatically. The separate close/release handshake from schema v3 no longer exists.

For `start_agent`, spawn one isolated agent with `fork_turns="none"` and pass only the emitted three-line prompt. Process every packet chunk. Write the result to the exact expected path. Run `validate-result` and repair that staged file until it passes; canonical ledgers must never be edited by a worker.

Use `fail` for the controlled TPM, RPM, API-rate, network, worker, process, spawn, or transient failure reason; the same job and packet remain authoritative. Retry count, reason, detail, delay, and next-at time are persisted. Delay follows the bounded, jitter-free exponential contract in `program_contract.py`; an explicit retry-after value is a capped lower bound. Use `recover-active` only when the assigned task cannot be resumed. At a controller checkpoint, report persistence and resume later with `resume`.

Packets contain compact source metadata, scoped graph records, versioned schemas, job contracts, and—only for a compound worker—the authoritative contract for that worker's perspective. They never contain raw-source bodies or other perspective contracts. Compound perspectives never receive another perspective's observations. Runtime JSON and JSONL artifacts are authoritative; conversation prose is not.

Schema v6 also prevents candidate leakage through claims, edges, and source metadata: compound perspectives receive broad-evidence context only. Their planned coverage is the unchanged generic compound contract plus the current lens's required coverage-area IDs; all use the existing terminal coverage states and frontier logic. Staged validation rejects semantic-enum drift, duplicate search operations, duplicate canonical sources or active moieties, incomplete generic or lens coverage, invalid frontier decisions, generic or incorrect lens rationales, route-normalized duplicate convergence narratives, packet-only audit searches, duplicate audit rationales, stale machine-derived caps, and class/endpoint-inconsistent council dispositions. Each compound perspective returns a source-linked `candidate_exclusions` list, including an empty list when nothing screened was excluded.

Schema v6 keeps the lean schema-v5 runtime design: no duplicated phase booleans, role maps, agent-release state, per-unit auditors, subtopic recursion state, council exchange state, proactive token accounting, or automatic final-repair loops. Job dependencies and canonical ledgers determine progress.

Completed schema-v3/v4/v5 runs are immutable historical artifacts. Schema-v6 controller commands reject them before writing; start a new schema-v6 run rather than inventing retrospective coverage, frontier, retry, or idempotency records.
