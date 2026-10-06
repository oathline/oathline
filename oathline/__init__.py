# SPDX-License-Identifier: MIT
"""Oathline: a small governance core for AI agents that take real actions.

The model interprets; deterministic code decides. Allowlisted capabilities,
a validator for model output, single-use confirmation tokens and a
hash-chained audit log. Standard library only.
"""
from .audit import AuditError, AuditLog
from .capabilities import Capability, Denied, Grant, Registry
from .engine import Engine, is_non_human
from .tokens import ConfigError, Confirmed, TokenStore
from .validator import FORBIDDEN_KEYS, Validated, validate

__version__ = "0.1.0"
__all__ = ["AuditError", "AuditLog", "Capability", "ConfigError", "Confirmed", "Denied", "Engine",
           "FORBIDDEN_KEYS", "Grant", "Registry", "TokenStore", "Validated", "__version__",
           "is_non_human", "validate"]
