"""Backward-compatible re-export; prefer ``components._sync``."""

from drissionpage_mcp.components._sync import with_read, with_write

__all__ = ["with_read", "with_write"]