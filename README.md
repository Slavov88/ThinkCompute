# ThinkCompute

ThinkCompute is a brokered framework for trace-based behavioral interpretability. Neural
deliberation, Python/SymPy, SMT, and Lean actions have explicit operational semantics, and
final correctness is independently verified.

## How it works

A model never gets a shell or filesystem. In each broker round it returns exactly one
JSON action (schema in `config/action_schema.json`):

| Action | Meaning |
|---|---|
| `THINK` | Deliberate; consumes one neural round |
| `PYTHON` | Run a validated Python/SymPy payload in a restricted worker |
| `SMT` | Run a validated Z3 payload in a restricted worker |
| `LEAN` | Submit a complete proof body for an intermediate Lean attempt |

The broker validates each action against the active policy, executes permitted payloads,
returns bounded output to the next round, and logs every externally consequential event.
Terminal success is decided by the harness, never by the model: the verifier rebuilds the
immutable theorem statement, rejects statement changes, placeholders and disallowed
declarations, and accepts only if Lean compiles the candidate.

The resource ledger records:

- `N`: neural rounds used
- `T` and `T_actual`: assigned deterministic budget and measured successful runtime
- `V`: intermediate Lean attempts before terminal verification

Assigned budgets are never substituted for measured consumption. A tool request is logged
even when validation or execution fails.

## Policies

Policy text is in `config/policies/`.

| Policy | First action | Deterministic compute | Repair rule |
|---|---|---|---|
| A0 | `THINK` | unavailable | free Lean feedback |
| A1 | free | optional | free |
| A2 | `THINK` | optional | a failed Lean attempt is followed by a non-Lean round |
| A3 | `THINK` | required once before `LEAN` | as A2 |
| A4 | `PYTHON` or `SMT` | required first | as A2 |

## Layout

```
harness/      broker, controller, action schema, budgets, tools, verifier, workers
config/       action schema, policy text, budget defaults, Lean environment template
container/    worker image definition and container-side tool launcher
examples/     runnable mock session
tests/        unit tests
```

Main modules: `broker.py` (round loop), `controller.py` (policy state machine),
`action_schema.py` (strict action parsing), `verifier.py` (terminal check),
`native_worker.py` and `docker_worker.py` (restricted tool workers), `benchmark.py`
(problem directory loader), `model_base.py` (model adapter interface).

## Requirements

- Python 3.10 or newer
- `sympy` and `z3-solver` (pinned in `requirements.txt`)
- Lean 4 with Mathlib, only for real verification (tested against Lean 4.33.1 and Mathlib v4.33.1)
- Docker, only for the container worker

## Start

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python examples/mock_run.py                  # full session with a scripted model, no Lean needed
python -m unittest discover -s tests -t .    # tests that need Lean are skipped when it is absent
```

## Connect Lean

Copy `config/verifier_environment.example.json` to `config/verifier_environment.json` and
fill in the paths, or set environment variables:

| Variable | Purpose |
|---|---|
| `THINKCOMPUTE_LEAN` | path to the `lean` executable (default: `lean` on `PATH`) |
| `THINKCOMPUTE_LEAN_PATH` | explicit `LEAN_PATH` for Mathlib |
| `THINKCOMPUTE_LEAN_TOOLCHAIN` | toolchain directory added to `LEAN_PATH` |
| `THINKCOMPUTE_DENY_MARKERS` | extra comma-separated path fragments the workers reject |

## Problem format

`harness/benchmark.py` loads a directory containing `manifest.json` and
`problems/<id>/Main.lean` plus `problems/<id>/problem.md`. The manifest lists `id`,
`split`, `main_sha256` and `statement_sha256` per problem, and can be pinned with
`Benchmark(root, expected_manifest_sha256=...)`. A `Main.lean` has the form:

```lean
namespace TC_0123ABCD
theorem target : <statement> := by
  sorry
end TC_0123ABCD
```

The verifier replaces the `by` / `sorry` placeholder with the candidate proof body.

## Use your own model

Implement `invoke_once(prompt, *, scratch, timeout)` returning a `RoundResponse`
(`harness/broker.py`) and pass it as `model` to `run_broker`. `ScriptedMockModel` in
`harness/model_mock.py` is a minimal reference. `harness/model_base.py` defines the
adapter base class for single-call runs.

```python
from harness.broker import BrokerConfig, run_broker

result = run_broker(theorem=theorem_text, policy="A2", model=my_model,
                    tool_worker=tool_worker, lean_worker=lean_worker,
                    final_verifier=final_verifier, scratch=scratch_dir,
                    config=BrokerConfig(n_limit=8, t_limit_seconds=10, v_intermediate_limit=6))
```

`result` holds the per-round trace and the ledger (`n_actual`, `t_actual_seconds`,
`v_intermediate_actual`), `termination_reason` and `verified`.

## Container worker

`harness/docker_worker.py` builds `docker run` commands with no network, a read-only
root, dropped capabilities and two explicit mounts. Build the image from a pinned base
that contains Python, SymPy, Z3, Lean and the Mathlib cache:

```bash
docker build -f container/Dockerfile \
  --build-arg THINKCOMPUTE_TOOLCHAIN_REF=<image digest> \
  --build-arg MATHLIB_COMMIT=<mathlib commit> \
  -t thinkcompute-tools:pinned .
```

## Isolation note

The native worker is a capability filter and process wrapper, not an operating-system
security boundary. When isolation from the host matters, run the whole process in a
restricted account, VM or container.
