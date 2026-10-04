"""Worker identity, profile, triage, and router configuration sections."""

from __future__ import annotations

from issuekit.coerce import optional_str
from issuekit.core import VALID_ISSUE_PRIORITIES, is_valid_workflow_token

from .model import (
    PROFILE_SUMMARY_MAX_LEN,
    PROFILE_TAG_MAX_LEN,
    PROFILE_TAGS_MAX,
    REPO_DESCRIPTION_MAX_LEN,
    WORKER_DESCRIPTION_MAX_LEN,
    WORKER_ROLE_MAX_LEN,
    IssuekitConfig,
    RouterPolicy,
    TriagePolicy,
    WorkerIdentity,
)
from .values import _bool_value, _config_value, _string_tuple


def _load_worker(raw: object) -> WorkerIdentity | None:
    if not isinstance(raw, dict):
        return None
    machine_id = _required_worker_value(raw, "machine_id")
    repo_id = _required_worker_value(raw, "repo_id")
    worker_id = _required_worker_value(raw, "worker_name")
    if not (machine_id and repo_id and worker_id):
        return None
    return WorkerIdentity(machine_id=machine_id, repo_id=repo_id, worker_id=worker_id)


def _worker_metadata(value: object, *, field: str, max_len: int) -> str:
    text = optional_str(value)
    if text is None:
        return ""
    if len(text) > max_len:
        raise ValueError(f"{field} must be at most {max_len} characters.")
    return text


def _metadata_table(value: object, *, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a table.")
    metadata: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key or not is_valid_workflow_token(key):
            raise ValueError(f"Invalid {field} key: {raw_key}")
        text = optional_str(raw_value)
        if text is None:
            continue
        metadata[key] = text
    return metadata


def _load_profile_tags(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    tags = _string_tuple(value)
    if len(tags) > PROFILE_TAGS_MAX:
        raise ValueError(f"profile_tags must have at most {PROFILE_TAGS_MAX} tags.")
    for tag in tags:
        if not tag or len(tag) > PROFILE_TAG_MAX_LEN or not is_valid_workflow_token(tag):
            raise ValueError(f"Invalid profile_tags token: {tag}")
    return tags


def _load_triage_policy(raw: object) -> TriagePolicy:
    if not raw:
        return TriagePolicy()
    if not isinstance(raw, dict):
        raise ValueError("triage config must be a table.")
    hold_auto_adopted = raw.get(
        "hold_auto_adopted",
        TriagePolicy.hold_auto_adopted,
    )
    if not isinstance(hold_auto_adopted, bool):
        raise ValueError("Invalid triage.hold_auto_adopted: must be a boolean.")
    default_priority = str(raw.get("default_priority", TriagePolicy.default_priority)).strip()
    if default_priority not in VALID_ISSUE_PRIORITIES:
        raise ValueError(f"Invalid triage.default_priority: {default_priority}")
    trusted_origins = _string_tuple(raw.get("trusted_origins", TriagePolicy.trusted_origins))
    invalid_origins = [
        origin for origin in trusted_origins if not origin or not is_valid_workflow_token(origin)
    ]
    if invalid_origins:
        raise ValueError(f"Invalid triage.trusted_origins token: {invalid_origins[0]}")
    max_adoptions = _config_value(
        "triage.max_adoptions_per_cycle",
        raw.get(
            "max_adoptions_per_cycle",
            TriagePolicy.max_adoptions_per_cycle,
        ),
        int,
    )
    if max_adoptions < 1:
        raise ValueError("triage.max_adoptions_per_cycle must be greater than zero.")
    author_agent = str(raw.get("author_agent", TriagePolicy.author_agent)).strip()
    if author_agent and not is_valid_workflow_token(author_agent):
        raise ValueError(f"Invalid triage.author_agent token: {author_agent}")
    return TriagePolicy(
        auto_adopt=_bool_value(raw.get("auto_adopt", TriagePolicy.auto_adopt)),
        hold_auto_adopted=hold_auto_adopted,
        trusted_origins=trusted_origins,
        default_priority=default_priority,
        require_blocking=_bool_value(raw.get("require_blocking", TriagePolicy.require_blocking)),
        max_adoptions_per_cycle=max_adoptions,
        author_agent=author_agent,
    )


def _load_router_policy(raw: object) -> RouterPolicy:
    if not raw:
        return RouterPolicy()
    if not isinstance(raw, dict):
        raise ValueError("router config must be a table.")
    agent = str(raw.get("agent", RouterPolicy.agent)).strip()
    if agent and not is_valid_workflow_token(agent):
        raise ValueError(f"Invalid router.agent token: {agent}")
    max_targets = _config_value(
        "router.max_targets",
        raw.get("max_targets", RouterPolicy.max_targets),
        int,
    )
    if max_targets < 1:
        raise ValueError("router.max_targets must be greater than zero.")
    max_clarify_rounds = _config_value(
        "router.max_clarify_rounds",
        raw.get("max_clarify_rounds", RouterPolicy.max_clarify_rounds),
        int,
    )
    if max_clarify_rounds < 0:
        raise ValueError("router.max_clarify_rounds must be zero or greater.")
    return RouterPolicy(
        agent=agent,
        max_targets=max_targets,
        max_clarify_rounds=max_clarify_rounds,
    )


def _required_worker_value(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    return "" if value is None else str(value).strip()


def load_identity_metadata(raw_config: dict[str, object]) -> dict[str, object]:
    worker = _load_worker(raw_config.get("worker"))
    worker_role = _worker_metadata(
        raw_config.get("worker_role"), field="worker_role", max_len=WORKER_ROLE_MAX_LEN
    )
    worker_description = _worker_metadata(
        raw_config.get("worker_description"),
        field="worker_description",
        max_len=WORKER_DESCRIPTION_MAX_LEN,
    )
    repo_description = _worker_metadata(
        raw_config.get("repo_description"),
        field="repo_description",
        max_len=REPO_DESCRIPTION_MAX_LEN,
    )
    repo_metadata = _metadata_table(raw_config.get("repo_metadata"), field="repo_metadata")
    worker_metadata = _metadata_table(raw_config.get("worker_metadata"), field="worker_metadata")
    profile_file = (
        str(raw_config.get("profile_file", IssuekitConfig.profile_file)).strip()
        or IssuekitConfig.profile_file
    )
    profile_summary = _worker_metadata(
        raw_config.get("profile_summary"),
        field="profile_summary",
        max_len=PROFILE_SUMMARY_MAX_LEN,
    )
    profile_tags = _load_profile_tags(raw_config.get("profile_tags"))
    return {
        "worker": worker,
        "worker_role": worker_role,
        "worker_description": worker_description,
        "worker_accept_directed": _bool_value(
            raw_config.get("worker_accept_directed", IssuekitConfig.worker_accept_directed)
        ),
        "repo_description": repo_description,
        "repo_metadata": repo_metadata,
        "worker_metadata": worker_metadata,
        "profile_file": profile_file,
        "profile_summary": profile_summary,
        "profile_tags": profile_tags,
    }
