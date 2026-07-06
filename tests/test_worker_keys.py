"""Tests for worker identity key helpers."""

from issuekit.worker_keys import (
    directed_target_matches,
    qualified_worker_key,
    worker_keys_from_row,
    worker_keys_match,
)


def test_qualified_worker_key_format() -> None:
    assert qualified_worker_key("pike3", "mine-py", "alpha") == "alpha.mine-py@pike3"


def test_worker_keys_match_dotted_pair() -> None:
    assert worker_keys_match("alpha.mine-py", "alpha.mine-py")
    assert not worker_keys_match("alpha.mine-py", "beta.mine-py")
    assert not worker_keys_match("alpha.mine-py", "alpha.other")


def test_worker_keys_match_legacy_stays_machine_agnostic() -> None:
    assert worker_keys_match("pike3/mine-py/alpha", "main1/mine-py/alpha")
    assert worker_keys_match("pike3/mine-py/alpha", "alpha.mine-py")


def test_worker_keys_match_qualified_discriminates_machine() -> None:
    assert worker_keys_match("alpha.mine-py@pike3", "alpha.mine-py@pike3")
    assert not worker_keys_match("alpha.mine-py@pike3", "alpha.mine-py@main1")


def test_worker_keys_match_bare_form_stays_machine_agnostic() -> None:
    assert worker_keys_match("alpha.mine-py@pike3", "alpha.mine-py")
    assert worker_keys_match("alpha.mine-py", "alpha.mine-py@main1")


def test_worker_keys_match_server_canonical_target_form() -> None:
    assert worker_keys_match("alpha@pike3", "alpha.mine-py@pike3")
    assert not worker_keys_match("alpha@pike3", "alpha.mine-py@main1")
    assert not worker_keys_match("alpha@pike3", "beta.mine-py@pike3")


def test_worker_keys_match_bare_worker_name_requires_exact() -> None:
    assert worker_keys_match("alpha", "alpha")
    assert not worker_keys_match("alpha", "alpha.mine-py")


def test_worker_keys_match_rejects_malformed_qualified_forms() -> None:
    assert not worker_keys_match("alpha.mine-py@", "alpha.mine-py")
    assert not worker_keys_match("@pike3", "alpha.mine-py@pike3")


def test_directed_target_matches_bare_target_is_machine_agnostic() -> None:
    assert directed_target_matches("alpha.mine-py", "alpha.mine-py@pike3")
    assert directed_target_matches("alpha", "alpha")


def test_directed_target_matches_qualified_target_requires_machine() -> None:
    assert directed_target_matches("alpha.mine-py@pike3", "alpha.mine-py@pike3")
    assert directed_target_matches("alpha@pike3", "alpha.mine-py@pike3")
    assert not directed_target_matches("alpha.mine-py@pike3", "alpha.mine-py@main1")
    assert not directed_target_matches("alpha.mine-py@pike3", "alpha.mine-py")


def test_worker_keys_from_row_includes_qualified_key() -> None:
    row = {
        "id": "pike3/mine-py/alpha",
        "machine_id": "pike3",
        "repo_id": "mine-py",
        "worker_name": "alpha",
    }
    keys = worker_keys_from_row(row)
    assert "alpha.mine-py" in keys
    assert "alpha.mine-py@pike3" in keys
    assert "pike3/mine-py/alpha" in keys


def test_worker_keys_from_row_without_machine_id_has_no_qualified_key() -> None:
    keys = worker_keys_from_row({"repo_id": "mine-py", "worker_name": "alpha"})
    assert keys == {"alpha.mine-py"}
