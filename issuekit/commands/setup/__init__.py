"""Setup command public surface."""

from .command import (
    CLIENT_TRANSPORT_CHECK,
    CODEX_MCP_ADD_COMMAND,
    MCP_INSTALL_COMMAND,
    MCP_REINSTALL_COMMAND,
    Diagnostic,
    build_check_json_payload,
    build_json_payload,
    collect_diagnostics,
    register,
    run,
)

__all__ = [
    "CLIENT_TRANSPORT_CHECK",
    "CODEX_MCP_ADD_COMMAND",
    "Diagnostic",
    "MCP_INSTALL_COMMAND",
    "MCP_REINSTALL_COMMAND",
    "build_check_json_payload",
    "build_json_payload",
    "collect_diagnostics",
    "register",
    "run",
]
