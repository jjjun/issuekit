"""Issue-lifecycle fake client surface."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from issuekit.core import directed_target_matches, drop_none, worker_keys_match
from issuekit.workflow import WorkflowError

JsonDict = dict[str, Any]
READY_STAGES = {"", "todo", "changes_requested"}
CLAIMABLE_STATUSES = {"active", "in_progress"}
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


class FakeIssueSurface:
    def count_issues(
        self,
        *,
        status: str | None = None,
        stage: str | None = None,
        assignee: str | None = None,
        include_completed: bool = False,
    ) -> int:
        return len(
            self.list_all_issues(
                status=status,
                stage=stage,
                assignee=assignee,
                include_completed=include_completed,
            )
        )

    def list_issues(
        self,
        *,
        status: str | None = None,
        stage: str | None = None,
        assignee: str | None = None,
        include_completed: bool = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[JsonDict]:
        with self._lock:
            issues = [
                issue
                for issue in sorted(self._issues.values(), key=lambda item: int(item["id"]))
                if (
                    (status is None and (include_completed or issue.get("status") != "completed"))
                    or (status is not None and issue.get("status") == status)
                )
                and (stage is None or issue.get("stage") == stage)
                and (assignee is None or issue.get("assignee") == assignee)
            ]
            start = offset or 0
            stop = start + (limit if limit is not None else 100)
            issues = issues[start:stop]
            return deepcopy(issues)

    def list_all_issues(
        self,
        *,
        status: str | None = None,
        stage: str | None = None,
        assignee: str | None = None,
        include_completed: bool = False,
        page_size: int = 500,
    ) -> list[JsonDict]:
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        page_size = min(page_size, 500)
        offset = 0
        issues: list[JsonDict] = []
        while True:
            batch = self.list_issues(
                status=status,
                stage=stage,
                assignee=assignee,
                include_completed=include_completed,
                limit=page_size,
                offset=offset,
            )
            issues.extend(batch)
            if len(batch) < page_size:
                return issues
            offset += page_size

    def get_issue(self, number: int) -> JsonDict:
        with self._lock:
            return deepcopy(self._find(number))

    def create_issue(self, issue: JsonDict, *, session: str | None = None) -> JsonDict:
        with self._lock:
            body = deepcopy(issue)
            if session is not None:
                body["session"] = session
            self._record("create_issue", body=deepcopy(body))
            stored = deepcopy(body)
            if (
                stored.get("target_worker")
                and self.stored_target_worker_override is not None
            ):
                stored["target_worker"] = self.stored_target_worker_override
            return deepcopy(self._store_issue(stored, allocate=True))

    def update_issue(self, number: int, issue: JsonDict) -> JsonDict:
        with self._lock:
            self._record("update_issue", number=number, body=deepcopy(issue))
            stored = self._find(number)
            stored.update(deepcopy(issue))
            return deepcopy(stored)

    def claim(
        self,
        number: int,
        *,
        assignee: str,
        worker: str | None = None,
        allow_self_implement: bool = False,
        session: str | None = None,
    ) -> JsonDict:
        with self._lock:
            body = drop_none({"assignee": assignee, "worker": worker, "session": session})
            if allow_self_implement:
                body["allow_self_implement"] = True
            self._record(
                "claim",
                number=number,
                body=body,
            )
            issue = self._find(number)
            self._claim_issue(
                issue,
                assignee,
                worker=worker,
                allow_self_implement=allow_self_implement,
                session=session,
            )
            response = deepcopy(issue)
            warning = _dependency_warning(issue)
            if warning:
                response["warnings"] = [warning]
            return response

    def claim_next(
        self,
        *,
        assignee: str,
        priority: str | None = None,
        worker: str | None = None,
        allow_self_implement: bool = False,
        session: str | None = None,
    ) -> JsonDict | None:
        with self._lock:
            body = drop_none(
                {
                    "assignee": assignee,
                    "priority": priority,
                    "worker": worker,
                    "session": session,
                }
            )
            if allow_self_implement:
                body["allow_self_implement"] = True
            self._record(
                "claim_next",
                body=body,
            )
            candidates = [
                issue
                for issue in self._issues.values()
                if issue.get("status") in CLAIMABLE_STATUSES
                and issue.get("stage", "") in READY_STAGES
                and issue.get("assignee", "") in {"", assignee}
                and (priority is None or issue.get("priority") == priority)
                and _matches_target_worker(issue, worker)
                and issue.get("dependency_state", "none") not in {"waiting", "attention"}
            ]
            if not candidates:
                return None
            issue = sorted(
                candidates,
                key=lambda item: (
                    PRIORITY_RANK.get(str(item.get("priority", "")), 99),
                    int(item["id"]),
                ),
            )[0]
            self._claim_issue(
                issue,
                assignee,
                worker=worker,
                allow_self_implement=allow_self_implement,
                session=session,
            )
            return deepcopy(issue)

    def reclaim(
        self,
        number: int,
        *,
        expected_worker: str | None = None,
        actor: str | None = None,
        reason: str | None = None,
    ) -> JsonDict:
        with self._lock:
            self._record(
                "reclaim",
                number=number,
                body=drop_none(
                    {
                        "expected_worker": expected_worker,
                        "actor": actor,
                        "reason": reason,
                    }
                ),
            )
            issue = self._find(number)
            if issue.get("stage", "") == "todo" and not issue.get("assignee") and not issue.get("worker"):
                return deepcopy(issue)
            if issue.get("stage", "") != "implementing":
                raise WorkflowError(
                    f"Issue #{number} is at stage {issue.get('stage') or 'todo'}, not implementing.",
                    code="invalid_transition",
                )
            if expected_worker is not None and issue.get("worker") != expected_worker:
                raise WorkflowError(
                    f"Issue #{number} is held by worker {issue.get('worker') or '-'}, not {expected_worker}.",
                    code="race_lost",
                )
            issue["status"] = "active"
            issue["stage"] = "todo"
            issue["assignee"] = ""
            issue["implementer"] = ""
            issue["worker"] = ""
            return deepcopy(issue)

    def readdress(
        self,
        number: int,
        *,
        expected_target_worker: str | None = None,
        actor: str | None = None,
        reason: str | None = None,
    ) -> JsonDict:
        with self._lock:
            self._record(
                "readdress",
                number=number,
                body=drop_none(
                    {
                        "expected_target_worker": expected_target_worker,
                        "actor": actor,
                        "reason": reason,
                    }
                ),
            )
            issue = self._find(number)
            target_worker = str(issue.get("target_worker") or "")
            if not target_worker:
                raise WorkflowError(
                    f"Issue #{number} is not directed to a worker.",
                    code="invalid_transition",
                )
            if expected_target_worker is not None and target_worker != expected_target_worker:
                raise WorkflowError(
                    f"Issue #{number} is directed to {target_worker}, not {expected_target_worker}.",
                    code="race_lost",
                )
            issue["target_worker"] = ""
            return deepcopy(issue)

    def dispatch(
        self,
        number: int,
        *,
        target_worker: str,
        assignee: str | None = None,
        stage: str | None = None,
    ) -> JsonDict:
        with self._lock:
            self._record(
                "dispatch",
                number=number,
                body=drop_none(
                    {
                        "target_worker": target_worker,
                        "assignee": assignee,
                        "stage": stage,
                    }
                ),
            )
            issue = self._find(number)
            current_stage = str(issue.get("stage") or "todo")
            if current_stage not in {"todo", "planned", "changes_requested"}:
                raise WorkflowError(
                    f"Issue #{number} is at stage {current_stage}, not dispatchable.",
                    code="invalid_transition",
                )
            issue["target_worker"] = (
                self.stored_target_worker_override
                if self.stored_target_worker_override is not None
                else target_worker
            )
            if assignee is not None:
                issue["assignee"] = assignee
            if stage is not None:
                issue["stage"] = stage
            return deepcopy(issue)

    def submit(
        self,
        number: int,
        *,
        summary: str,
        branch: str | None = None,
        commit: str | None = None,
        reviewer: str | None = None,
        session: str | None = None,
        agent_model: str | None = None,
        agent_reasoning_effort: str | None = None,
    ) -> JsonDict:
        with self._lock:
            self._record(
                "submit",
                number=number,
                body=drop_none(
                    {
                        "summary": summary,
                        "branch": branch,
                        "commit": commit,
                        "reviewer": reviewer,
                        "session": session,
                        "agent_model": agent_model,
                        "agent_reasoning_effort": agent_reasoning_effort,
                    }
                ),
            )
            issue = self._find(number)
            if reviewer and issue.get("implementer") == reviewer:
                raise WorkflowError(
                    f"Issue #{number} was implemented by {reviewer}; self-review is not allowed.",
                    code="invalid_transition",
                )
            if session is not None:
                issue["implementer_session"] = session
            issue["stage"] = "review"
            issue["assignee"] = reviewer or ""
            issue["summary"] = summary
            if branch is not None:
                issue["branch"] = branch
            if commit is not None:
                issue["commit"] = commit
            return deepcopy(issue)

    def request_changes(
        self,
        number: int,
        *,
        notes: str,
        reviewer: str | None = None,
        assignee: str | None = None,
        worker: str | None = None,
        session: str | None = None,
        agent_model: str | None = None,
        agent_reasoning_effort: str | None = None,
    ) -> JsonDict:
        with self._lock:
            self._record(
                "request_changes",
                number=number,
                body=drop_none(
                    {
                        "notes": notes,
                        "reviewer": reviewer,
                        "assignee": assignee,
                        "worker": worker,
                        "session": session,
                        "agent_model": agent_model,
                        "agent_reasoning_effort": agent_reasoning_effort,
                    }
                ),
            )
            issue = self._find(number)
            if session is not None:
                issue["reviewer_session"] = session
            issue["stage"] = "changes_requested"
            issue["assignee"] = assignee or issue.get("implementer") or "codex"
            return deepcopy(issue)

    def approve(
        self,
        number: int,
        *,
        summary: str,
        verification: str,
        reviewer: str,
        worker: str | None = None,
        session: str | None = None,
        agent_model: str | None = None,
        agent_reasoning_effort: str | None = None,
    ) -> JsonDict:
        with self._lock:
            self._record(
                "approve",
                number=number,
                body=drop_none(
                    {
                        "summary": summary,
                        "verification": verification,
                        "reviewer": reviewer,
                        "worker": worker,
                        "session": session,
                        "agent_model": agent_model,
                        "agent_reasoning_effort": agent_reasoning_effort,
                    }
                ),
            )
            issue = self._find(number)
            if issue.get("stage", "") != "review":
                raise WorkflowError(
                    f"Issue #{number} is not at the review stage.",
                    code="invalid_transition",
                )
            if _is_self_review(issue, reviewer, worker):
                raise WorkflowError(
                    f"Issue #{number} was implemented by {reviewer}; self-review is not allowed.",
                    code="invalid_transition",
                )
            if session is not None:
                issue["reviewer_session"] = session
            issue["status"] = "completed"
            issue["stage"] = "done"
            issue["assignee"] = ""
            return deepcopy(issue)

    def complete(
        self,
        number: int,
        *,
        summary: str,
        verification: str,
        force: bool = False,
        agent_model: str | None = None,
        agent_reasoning_effort: str | None = None,
    ) -> JsonDict:
        with self._lock:
            self._record(
                "complete",
                number=number,
                body=drop_none(
                    {
                        "summary": summary,
                        "verification": verification,
                        "force": force,
                        "agent_model": agent_model,
                        "agent_reasoning_effort": agent_reasoning_effort,
                    }
                ),
            )
            issue = self._find(number)
            issue["status"] = "completed"
            issue["stage"] = "done"
            issue["assignee"] = ""
            return deepcopy(issue)

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
        worker_id: str | None = None,
        worker_name: str | None = None,
        path: str | None = None,
        canonical_url: str | None = None,
        project: str | None = None,
        role: str | None = None,
        description: str | None = None,
        repo_description: str | None = None,
        repo_metadata: dict[str, str] | None = None,
        worker_metadata: dict[str, str] | None = None,
        meta: dict[str, str] | None = None,
        accept_directed: bool | None = None,
    ) -> JsonDict:
        resolved_worker_name = worker_name or worker_id
        if not resolved_worker_name:
            raise WorkflowError("worker_name is required.", code="invalid_value")
        resolved_meta = meta if meta is not None else worker_metadata
        body = {
            "machine_id": machine_id,
            "repo_id": repo_id,
            "repo_key": repo_id,
            "worker_name": resolved_worker_name,
            "path": path,
        }
        if project is not None:
            body["project"] = project
        if role is not None:
            body["role"] = role
        if description is not None:
            body["description"] = description
        if resolved_meta is not None:
            body["meta"] = deepcopy(resolved_meta)
        if accept_directed is not None:
            body["accept_directed"] = accept_directed
        with self._lock:
            self._record("upsert_worker", body=body)
            repo = self._repos.get(repo_id, {})
            record = {
                "id": f"{resolved_worker_name}.{repo_id}",
                "machine_id": machine_id,
                "repo_id": repo_id,
                "repo_key": repo_id,
                "worker_name": resolved_worker_name,
                "path": path,
                "canonical_url": repo.get("canonical_url", canonical_url),
                "project": project,
                "role": role,
                "description": description,
                "repo_description": repo.get("description", repo_description),
                "repo_metadata": deepcopy(repo.get("meta", repo_metadata or {})),
                "worker_metadata": deepcopy(resolved_meta or {}),
                "meta": deepcopy(resolved_meta or {}),
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

    def _find(self, number: int) -> JsonDict:
        issue = self._issues.get(number)
        if issue is None:
            raise WorkflowError(f"Issue #{number} was not found.", code="not_found")
        return issue

    def _store_issue(self, issue: JsonDict, *, allocate: bool = False) -> JsonDict:
        stored = deepcopy(issue)
        raw_id = stored.get("id", stored.get("number"))
        if allocate or raw_id is None:
            issue_id = self._next_id
            self._next_id += 1
        else:
            issue_id = int(raw_id)
            self._next_id = max(self._next_id, issue_id + 1)
        stored["id"] = issue_id
        stored.pop("number", None)
        stored.setdefault("title", f"Issue #{issue_id}")
        stored.setdefault("status", "active")
        stored.setdefault("priority", "medium")
        stored.setdefault("created", "2026-01-01")
        stored.setdefault("completed", "")
        stored.setdefault("assignee", "")
        stored.setdefault("stage", "todo")
        stored.setdefault("implementer", "")
        stored.setdefault("author", "")
        stored.setdefault("worker", "")
        stored.setdefault("target_worker", "")
        stored.setdefault("author_session", stored.pop("session", ""))
        stored.setdefault("implementer_session", "")
        stored.setdefault("reviewer_session", "")
        stored.setdefault("body", "")
        self._issues[issue_id] = stored
        return stored

    def _claim_issue(
        self,
        issue: JsonDict,
        assignee: str,
        *,
        worker: str | None = None,
        allow_self_implement: bool = False,
        session: str | None = None,
    ) -> None:
        issue_id = issue["id"]
        if issue.get("status") not in CLAIMABLE_STATUSES:
            raise WorkflowError(
                f"Issue #{issue_id} has status {issue.get('status')}; "
                "only active or in_progress issues can be implemented.",
                code="invalid_transition",
            )
        if issue.get("stage", "") not in READY_STAGES | {"implementing"}:
            raise WorkflowError(
                f"Issue #{issue_id} is at stage {issue.get('stage') or 'todo'}, not ready for implementation.",
                code="invalid_transition",
            )
        if issue.get("assignee", "") not in {"", assignee}:
            raise WorkflowError(
                f"Issue #{issue_id} is assigned to {issue.get('assignee')}, not {assignee}.",
                code="invalid_transition",
            )
        if not _matches_target_worker(issue, worker):
            raise WorkflowError(
                f"Issue #{issue_id} is directed to worker "
                f"{issue.get('target_worker')}, not {worker or '-'}.",
                code="invalid_transition",
            )
        if not allow_self_implement and issue.get("author") == assignee:
            if issue.get("author_session") and session and issue.get("author_session") != session:
                pass
            else:
                raise WorkflowError(
                    f"Issue #{issue_id} was authored by {assignee}; self-implementation is not allowed.",
                    code="invalid_transition",
                )
        issue["status"] = "in_progress"
        issue["assignee"] = assignee
        issue["stage"] = "implementing"
        issue["implementer"] = assignee
        if session is not None:
            issue["implementer_session"] = session
        if worker is not None:
            issue["worker"] = worker


def _dependency_warning(issue: JsonDict) -> str:
    state = str(issue.get("dependency_state") or "")
    if state not in {"waiting", "attention"}:
        return ""
    return (
        f"Issue #{issue.get('id')} has dependency_state={state}; "
        "check upstream dependencies before implementing."
    )


def _is_self_review(issue: JsonDict, reviewer: str, reviewer_worker: str | None) -> bool:
    if issue.get("implementer") != reviewer:
        return False
    implementer_worker = str(issue.get("worker") or "")
    if implementer_worker and reviewer_worker:
        return worker_keys_match(implementer_worker, reviewer_worker)
    return True


def _matches_target_worker(issue: JsonDict, worker: str | None) -> bool:
    target_worker = str(issue.get("target_worker") or "")
    if not target_worker:
        return True
    return directed_target_matches(target_worker, worker or "")
