"""Worker and repository API resources."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote

from issuekit.core import drop_none

from .base import JsonDict, ensure_dict, worker_rows


class WorkerResourceMixin:
    def upsert_repo(
        self,
        *,
        repo_key: str,
        canonical_url: str | None = None,
        description: str | None = None,
        meta: Mapping[str, str] | None = None,
    ) -> JsonDict:
        body = drop_none(
            {
                "repo_key": repo_key,
                "canonical_url": canonical_url,
                "description": description,
                "meta": dict(meta) if meta is not None else None,
            }
        )
        payload = self._authorized_request("POST", "/api/repos", json=body)
        return ensure_dict(payload, "Repo response")

    def upsert_worker(
        self,
        *,
        machine_id: str,
        repo_id: str,
        worker_name: str,
        path: str | None = None,
        project: str | None = None,
        role: str | None = None,
        description: str | None = None,
        meta: Mapping[str, str] | None = None,
        accept_directed: bool | None = None,
    ) -> JsonDict:
        body = {
            "machine_id": machine_id,
            "repo_id": repo_id,
            "worker_name": worker_name,
            "path": path,
        }
        body.update(
            drop_none(
                {
                    "project": project,
                    "role": role,
                    "description": description,
                    "meta": dict(meta) if meta is not None else None,
                    "accept_directed": accept_directed,
                }
            )
        )
        payload = self._authorized_request("POST", "/api/workers", json=body)
        return ensure_dict(payload, "Worker response")

    def list_workers(
        self,
        *,
        repo_id: str | None = None,
        project: str | None = None,
    ) -> list[JsonDict]:
        payload = self._authorized_request(
            "GET",
            "/api/workers",
            params=drop_none({"repo_id": repo_id, "project": project}),
        )
        return worker_rows(payload)

    def delete_worker(self, worker_id: str) -> JsonDict:
        payload = self._authorized_request(
            "DELETE",
            f"/api/workers/{_quoted_path_segment(worker_id, label='worker id')}",
        )
        if payload is None:
            return {"id": worker_id, "deleted": True}
        return ensure_dict(payload, "Worker delete response")

    def delete_repo(self, repo_key: str) -> JsonDict:
        payload = self._authorized_request(
            "DELETE",
            f"/api/repos/{_quoted_path_segment(repo_key, label='repo key')}",
        )
        if payload is None:
            return {"repo_key": repo_key, "deleted": True}
        return ensure_dict(payload, "Repo delete response")


def _quoted_path_segment(value: str, *, label: str) -> str:
    parts = value.split("/")
    if not value or "\\" in value or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Invalid {label} path segment: {value}")
    return quote(value, safe="")
