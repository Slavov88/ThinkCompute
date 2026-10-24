# A3 - forced deterministic exploration

This policy uses the flat action schema and the brokered tool interface.
Round 1 must be `THINK`. Before `LEAN` becomes available, the broker requires one
successfully executed `PYTHON` or `SMT` action. The action must pass validation and
complete within the remaining deterministic budget; `compute_useful=false` does not
bypass the gate. After the gate, the A2 repair rule applies: a failed `LEAN` attempt
removes `LEAN` from the immediately following round.
