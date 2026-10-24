class ModelInvocationError(RuntimeError):
    """A model call failed (process exit, HTTP error, timeout, missing credentials).

    The harness never retries a failed model call; the session is recorded as an
    infrastructure error so a failed round cannot silently change the trace.
    """
