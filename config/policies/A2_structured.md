# A2 - structured allocator

1. Classify the mathematical structure.
2. Decide whether deterministic computation is useful.
3. Explore or falsify when useful.
4. Extract structure and formulate a proof plan.
5. Test fragile intermediate claims.
6. Return exactly one flat action object. Submit the current complete proof body
   with a LEAN action whenever plausible. Do not return a FINAL action or declare
   success; the harness determines success from Lean.
7. If Lean fails, use its compact diagnostics in a later round to repair the
   candidate. The run ends automatically when Lean verifies or the round cap is
   exhausted.

The model is not required to consume any unused budget.
