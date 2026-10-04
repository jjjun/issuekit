"""Project-profile fake client surface."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from issuekit.errors import WorkflowError

JsonDict = dict[str, Any]


class FakeProfileSurface:
    def put_project_profile(
        self,
        *,
        summary: str | None = None,
        profile_md: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        source_commit: str | None = None,
        source_committed_at: str | None = None,
    ) -> JsonDict:
        with self._lock:
            body = {
                "project": self.project,
                "summary": summary,
                "profile_md": profile_md,
                "tags": list(tags) if tags is not None else None,
                "source_commit": source_commit,
                "source_committed_at": source_committed_at,
            }
            self._record("put_project_profile", body=deepcopy(body))
            self._profiles[self.project] = deepcopy(body)
            return deepcopy(body)

    def get_project_profile(self, project: str | None = None) -> JsonDict:
        target = project or self.project
        with self._lock:
            profile = self._profiles.get(target)
            if profile is None:
                raise WorkflowError(
                    f"Project profile for {target} was not found.", code="http_404"
                )
            return deepcopy(profile)

    def list_project_profiles(self) -> list[JsonDict]:
        with self._lock:
            return [deepcopy(profile) for _, profile in sorted(self._profiles.items())]

    def register_catalog_project(
        self, project: str, *, summary: str | None = None
    ) -> None:
        """Seed a project in the profile catalog so proposal target validation accepts it."""
        with self._lock:
            self._profiles[project] = {
                "project": project,
                "summary": summary,
                "profile_md": None,
                "tags": None,
                "source_commit": None,
                "source_committed_at": None,
            }
