# Production worker base. Supply an audited, immutable Linux toolchain image.
# Sage is intentionally not a v1 requirement.
ARG THINKCOMPUTE_TOOLCHAIN_REF
FROM ${THINKCOMPUTE_TOOLCHAIN_REF}

USER root
WORKDIR /opt/thinkcompute
COPY container/tc_tool.py /opt/thinkcompute/tc_tool.py
RUN chmod 0555 /opt/thinkcompute/tc_tool.py

# The build must be supplied a pinned Mathlib commit. Do not silently use a
# floating branch in a reproducible build. The supplied base must contain
# Python, SymPy, Z3, Lean 4.33.1, and the matching Mathlib cache.
ARG MATHLIB_COMMIT
RUN test -n "${MATHLIB_COMMIT}" || (echo 'MATHLIB_COMMIT is required' >&2; exit 2)

# Network/package installation is intentionally left to the audited image
# build environment. The final image should contain the pinned Lean toolchain,
# Mathlib cache, Python, Sage, and SAT/SMT runtime before it is accepted.
USER sage
WORKDIR /workspace
