"""Public API for cross-repository proposal helpers."""

from .model import Proposal, ProposalError, origin_destination

__all__ = ["Proposal", "ProposalError", "origin_destination"]
