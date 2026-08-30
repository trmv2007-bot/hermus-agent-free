"""Compatibility adapters (Rebuild spec §5, §18).

Holds superseded modules that still have live consumers during the migration
window. Every module here is either a thin wrapper or a read-only reader. Each
has an explicit removal milestone and is **never** a production write path for a
canonical subsystem. Once its consumers are migrated it is deleted.
"""

__all__ = ["legacy_memory"]
