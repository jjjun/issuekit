"""API client and project catalog helpers for proposals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from issuekit.api import IssuekitClient
from issuekit.api.factory import client_for, require_api_url
from issuekit.config import IssuekitConfig
from issuekit.core import parse_target_address

from .model import ProposalError


def api_client(config: IssuekitConfig, *, project: str | None = None) -> IssuekitClient:
    require_api_url(config, "Proposal command", error=ProposalError)
    return client_for(
        config,
        project=project,
        keepalive=True,
    )


def _is_own_origin(origin: object, project: str) -> bool:
    return isinstance(origin, str) and origin.startswith(f"{project}#")


def validate_target_project(
    config: IssuekitConfig,
    target_project: str,
    *,
    client: IssuekitClient | None = None,
) -> None:
    """Validate proposal targets against the API's project catalog."""
    target_project = _target_repo(target_project, label="target project")
    projects = fetch_project_catalog(config, client=client)
    if target_project not in projects:
        raise ProposalError(_unknown_target_project_message(target_project, projects))


def fetch_project_catalog(
    config: IssuekitConfig,
    *,
    client: IssuekitClient | None = None,
) -> tuple[str, ...]:
    if client is not None:
        return _fetch_project_catalog(client)
    with api_client(config) as catalog_client:
        return _fetch_project_catalog(catalog_client)


def _fetch_project_catalog(client: IssuekitClient) -> tuple[str, ...]:
    profile_projects = _project_names_from_rows(client.list_project_profiles())
    worker_projects = _project_names_from_rows(client.list_workers())
    return tuple(sorted(set(profile_projects) | set(worker_projects)))


def _project_names_from_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    projects: list[str] = []
    for row in rows:
        project = str(row.get("project") or "").strip()
        if project and project not in projects:
            projects.append(project)
    return tuple(sorted(projects))


def _unknown_target_project_message(target_project: str, known_projects: Sequence[str]) -> str:
    if not known_projects:
        return f"Unknown target project '{target_project}'. No registered API projects were returned."
    preview = ", ".join(known_projects[:8])
    if len(known_projects) > 8:
        return (
            f"Unknown target project '{target_project}'. "
            f"{len(known_projects)} registered API projects are available; "
            f"first projects: {preview}."
        )
    return (
        f"Unknown target project '{target_project}'. "
        f"Registered API projects: {preview}."
    )


def _target_repo(value: str, *, label: str) -> str:
    try:
        return parse_target_address(value, label=label).repo
    except ValueError as exc:
        raise ProposalError(str(exc)) from exc


def proposal_id_arg(value: str) -> int:
    try:
        proposal_id = int(value)
    except ValueError as exc:
        raise ProposalError(f"Proposal id must be an integer: {value}") from exc
    if proposal_id <= 0:
        raise ProposalError(f"Proposal id must be positive: {value}")
    return proposal_id
