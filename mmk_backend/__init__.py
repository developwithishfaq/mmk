"""
MMK FastAPI backend layout (API layer split from optional /ui static bundle).

- runtime_state: single process runtime (session, sockets, caches) — swap in tests via dependency_overrides
- settings: env-backed configuration
- api.deps: FastAPI dependencies (get_runtime)
- contracts: Protocols for typing and future backends
"""

from mmk_backend.runtime_state import RuntimeState, runtime

__all__ = ["RuntimeState", "runtime"]
