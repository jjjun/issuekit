"""Tests for worker identity key helpers."""

from issuekit.core import (
    qualified_worker_key,
    worker_key_matches_row,
    worker_keys_from_row,
    worker_keys_match,
)


def test_qualified_worker_key_format() -> None:
    assert qualified_worker_key("pike3", "mine-py", "alpha") == "alpha.mine-py@pike3"


def test_worker_keys_match_dotted_pair() -> None:
    assert worker_keys_match("alpha.mine-py", "alpha.mine-py")
    assert not worker_keys_match("alpha.mine-py", "beta.mine-py")
    assert not worker_keys_match("alpha.mine-py", "alpha.other")


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


def test_worker_keys_match_bare_directed_target_is_machine_agnostic() -> None:
    assert worker_keys_match(
        "alpha.mine-py",
        "alpha.mine-py@pike3",
        require_target_machine=True,
    )
    assert worker_keys_match("alpha", "alpha", require_target_machine=True)


def test_worker_keys_match_qualified_directed_target_requires_machine() -> None:
    assert worker_keys_match(
        "alpha.mine-py@pike3",
        "alpha.mine-py@pike3",
        require_target_machine=True,
    )
    assert worker_keys_match(
        "alpha@pike3",
        "alpha.mine-py@pike3",
        require_target_machine=True,
    )
    assert not worker_keys_match(
        "alpha.mine-py@pike3",
        "alpha.mine-py@main1",
        require_target_machine=True,
    )
    assert not worker_keys_match(
        "alpha.mine-py@pike3",
        "alpha.mine-py",
        require_target_machine=True,
    )


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
    assert keys == {"alpha.mine-py", "alpha.mine-py@pike3"}


def test_worker_keys_from_row_without_machine_id_has_no_qualified_key() -> None:
    keys = worker_keys_from_row({"repo_id": "mine-py", "worker_name": "alpha"})
    assert keys == {"alpha.mine-py"}


def test_worker_key_matches_qualified_row_without_bare_alias_fallback() -> None:
    main1 = {
        "machine_id": "main1",
        "repo_id": "mine-py",
        "worker_name": "alpha",
    }
    pike3 = {
        "machine_id": "pike3",
        "repo_id": "mine-py",
        "worker_name": "alpha",
    }

    assert worker_key_matches_row("alpha.mine-py@main1", main1)
    assert not worker_key_matches_row("alpha.mine-py@main1", pike3)
    assert worker_key_matches_row("alpha.mine-py", main1)
    assert worker_key_matches_row("alpha.mine-py", pike3)
