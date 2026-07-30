"""Tests for directed worker address validation."""

import pytest

from issuekit.config import IssuekitConfig
from issuekit.workers.addressing import (
    resolve_registered_worker_address,
    target_worker_repo_id,
    validate_target_worker,
)
from issuekit.workflow import WorkflowError

CONFIG = IssuekitConfig(project="demo")
REGISTERED_WORKER = {
    "machine_id": "machine",
    "repo_id": "demo",
    "worker_name": "checkout",
}


def test_validate_target_worker_accepts_bare_registered_address() -> None:
    assert (
        validate_target_worker(
            "checkout.demo",
            config=CONFIG,
            workers=[REGISTERED_WORKER],
        )
        == "checkout.demo"
    )


def test_validate_target_worker_accepts_matching_machine_address() -> None:
    assert (
        validate_target_worker(
            "checkout.demo@machine",
            config=CONFIG,
            workers=[REGISTERED_WORKER],
        )
        == "checkout.demo@machine"
    )


@pytest.mark.parametrize(
    ("address", "workers"),
    [
        ("checkout.demo@other", [REGISTERED_WORKER]),
        ("missing.demo", [REGISTERED_WORKER]),
        (
            "checkout.demo@machine",
            [{"repo_id": "demo", "worker_name": "checkout"}],
        ),
    ],
)
def test_validate_target_worker_rejects_unmatched_address(
    address: str,
    workers: list[dict[str, str]],
) -> None:
    with pytest.raises(WorkflowError) as exc_info:
        validate_target_worker(address, config=CONFIG, workers=workers)

    assert exc_info.value.code == "worker_not_found"


def test_validate_target_worker_allows_unregistered_address_with_override() -> None:
    assert (
        validate_target_worker(
            "future.demo@machine",
            config=CONFIG,
            workers=[],
            allow_unregistered=True,
        )
        == "future.demo@machine"
    )


@pytest.mark.parametrize("address", ["", "   "])
def test_validate_target_worker_rejects_empty_address(address: str) -> None:
    with pytest.raises(WorkflowError) as exc_info:
        validate_target_worker(address, config=CONFIG, workers=[])

    assert exc_info.value.code == "invalid_worker"


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("worker.repo@machine", "repo"),
        ("worker.repo", "repo"),
        ("my.worker.repo@m", "repo"),
        ("worker", None),
    ],
)
def test_target_worker_repo_id(address: str, expected: str | None) -> None:
    assert target_worker_repo_id(address) == expected


def test_resolve_registered_worker_address_auto_selects_qualified_key() -> None:
    assert (
        resolve_registered_worker_address(
            [REGISTERED_WORKER],
            project="demo",
        )
        == "checkout.demo@machine"
    )


def test_resolve_registered_worker_address_normalizes_server_form() -> None:
    assert (
        resolve_registered_worker_address(
            [REGISTERED_WORKER],
            project="demo",
            address="checkout@machine",
        )
        == "checkout.demo@machine"
    )


def test_resolve_registered_worker_address_skips_unusable_worker_rows() -> None:
    assert (
        resolve_registered_worker_address(
            [{}, REGISTERED_WORKER],
            project="demo",
            address="checkout.demo@machine",
        )
        == "checkout.demo@machine"
    )


def test_resolve_registered_worker_address_rejects_ambiguous_bare_key() -> None:
    workers = [
        REGISTERED_WORKER,
        {
            "machine_id": "other",
            "repo_id": "demo",
            "worker_name": "checkout",
        },
    ]

    with pytest.raises(WorkflowError) as exc_info:
        resolve_registered_worker_address(
            workers,
            project="demo",
            address="checkout.demo",
        )

    assert exc_info.value.code == "worker_ambiguous"
    assert "checkout.demo@machine" in str(exc_info.value)
    assert "checkout.demo@other" in str(exc_info.value)
