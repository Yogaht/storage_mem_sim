"""Workload generators layered above :class:`MemoryEngine`.

Workloads translate application-level access semantics into the byte-address,
byte-size, and request-type lists accepted by ``MemoryEngine.issue_request``.
They do not depend on a particular media backend.
"""

