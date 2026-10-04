"""Worker and repository fake client surface."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from issuekit.errors import WorkflowError

JsonDict = dict[str, Any]


class FakeWorkerSurface:
    def upsert_repo(
        self,
        *,
        repo_key: str,
        canonical_url: str | None = None,
        description: str | None = None,
        meta: dict[str, str] | None = None,
    ) -> JsonDict:
        body = {
            "repo_key": repo_key,
            "canonical_url": canonical_url,
            "description": description,
            "meta": deepcopy(meta or {}),
        }
        with self._lock:
            self._record("upsert_repo", body=body)
            record = {
                "repo_key": repo_key,
                "canonical_url": canonical_url,
                "description": description,
                "meta": deepcopy(meta or {}),
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
            self._repos[repo_key] = record
            return deepcopy(record)

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
        meta: dict[str, str] | None = None,
        accept_directed: bool | None = None,
    ) -> JsonDict:
        body = {
            "machine_id": machine_id,
            "repo_id": repo_id,
            "worker_name": worker_name,
            "path": path,
        }
        if project is not None:
            body["project"] = project
        if role is not None:
            body["role"] = role
        if description is not None:
            body["description"] = description
        if meta is not None:
            body["meta"] = deepcopy(meta)
        if accept_directed is not None:
            body["accept_directed"] = accept_directed
        with self._lock:
            self._record("upsert_worker", body=body)
            repo = self._repos.get(repo_id, {})
            record = {
                "id": f"{worker_name}.{repo_id}",
                "machine_id": machine_id,
                "repo_id": repo_id,
                "worker_name": worker_name,
                "path": path,
                "canonical_url": repo.get("canonical_url"),
                "project": project,
                "role": role,
                "description": description,
                "repo_description": repo.get("description"),
                "repo_metadata": deepcopy(repo.get("meta", {})),
                "worker_metadata": deepcopy(meta or {}),
                "meta": deepcopy(meta or {}),
                "accept_directed": bool(accept_directed),
                "status": "idle",
                "current_issue": None,
                "last_seen": "2026-01-01T00:00:00Z",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
            self._workers[record["id"]] = record
            return deepcopy(record)

    def list_workers(
        self,
        *,
        repo_id: str | None = None,
        project: str | None = None,
    ) -> list[JsonDict]:
        with self._lock:
            self._record(
                "list_workers",
                body={"repo_id": repo_id, "project": project},
            )
            return [
                deepcopy(worker)
                for worker in self._workers.values()
                if repo_id is None or worker.get("repo_id") == repo_id
                if project is None or worker.get("project") == project
            ]

    def delete_worker(self, worker_id: str) -> JsonDict:
        with self._lock:
            self._record("delete_worker", body={"id": worker_id})
            worker = self._workers.pop(worker_id, None)
            if worker is None:
                raise WorkflowError(f"Worker {worker_id} was not found.", code="not_found")
            return {"id": worker_id, "deleted": True}

    def delete_repo(self, repo_key: str) -> JsonDict:
        with self._lock:
            self._record("delete_repo", body={"repo_key": repo_key})
            if repo_key not in self._repos:
                raise WorkflowError(f"Repo {repo_key} was not found.", code="not_found")
            self._repos.pop(repo_key)
            return {"repo_key": repo_key, "deleted": True}
