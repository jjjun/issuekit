"""Proposal operations shared by commands and integrations."""

from __future__ import annotations

from issuekit.config import IssuekitConfig

from .client import api_client


def list_incoming_proposals(config: IssuekitConfig) -> list[dict]:
    with api_client(config) as client:
        return client.list_proposals(status="pending")
