"""Message Flow — composition shim.

Re-exports MessageRoutingMixin and MessageExecutionMixin for backward
compatibility.  All code has been moved to message_routing.py and
message_execution.py.
"""
from .message_routing import MessageRoutingMixin
from .message_execution import MessageExecutionMixin

__all__ = ["MessageRoutingMixin", "MessageExecutionMixin"]
