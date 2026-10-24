# ThinkCompute worker image

This directory contains the image definition and the container-side narrow-tool
launcher. Build it from a pinned toolchain base image.

Required build inputs:

```text
THINKCOMPUTE_TOOLCHAIN_REF=<immutable OCI image digest>
MATHLIB_COMMIT=<immutable mathlib commit>
```

The base image must contain Python, SymPy, Z3, Lean 4.33.1, and the matching
Mathlib cache.

Runs use explicit resource limits and explicit mounts from the host harness.
The root `.dockerignore` limits the build context to `container/tc_tool.py`.
