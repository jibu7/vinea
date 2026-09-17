"""Fiscalization: the adapter boundary, the DTOs, and the country implementations.

Rule 12 lives here. `protocol.py` is the interface, `mapping.py` the country-neutral DTOs,
`null.py` the adapter for a company that does not fiscalize, and `rwanda/` the one country
this phase implements. **No module outside this package imports `rwanda/`** —
`tests/fiscal/test_boundary.py` is the enforcement, and `adapter_for()` below is why nothing
needs to.
"""

from app.fiscal.registry import adapter_for

__all__ = ["adapter_for"]
