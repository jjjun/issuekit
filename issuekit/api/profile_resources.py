"""Project profile API resources."""

from __future__ import annotations

from collections.abc import Sequence

from issuekit.core import drop_none

from .base import JsonDict, _validate_project_token, ensure_dict, profile_rows


class ProfileResourceMixin:
    project: str

    def put_project_profile(
        self,
        *,
        summary: str | None = None,
        profile_md: str | None = None,
        tags: Sequence[str] | None = None,
        source_commit: str | None = None,
        source_committed_at: str | None = None,
    ) -> JsonDict:
        body = drop_none(
            {
                "summary": summary,
                "profile_md": profile_md,
                "tags": list(tags) if tags is not None else None,
                "source_commit": source_commit,
                "source_committed_at": source_committed_at,
            }
        )
        payload = self._authorized_request(
            "PUT",
            f"/api/projects/{self.project}/profile",
            json=body,
        )
        return ensure_dict(payload, "Project profile response")

    def get_project_profile(self, project: str | None = None) -> JsonDict:
        target = self.project if project is None else project
        _validate_project_token(target)
        payload = self._authorized_request(
            "GET",
            f"/api/projects/{target}/profile",
        )
        return ensure_dict(payload, "Project profile response")

    def list_project_profiles(self) -> list[JsonDict]:
        payload = self._authorized_request("GET", "/api/projects/profiles")
        return profile_rows(payload)
