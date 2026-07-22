def api_issue(
    issue_id: int,
    title: str,
    *,
    status: str = "active",
    priority: str = "medium",
    created: str = "2026-01-01",
    completed: str = "",
    assignee: str = "",
    stage: str = "",
    implementer: str = "",
    author: str = "",
    worker: str = "",
    target_worker: str = "",
    origin: str = "",
    depends_on: list[str] | None = None,
    dependency_state: str | None = None,
    dependencies: list[dict[str, object]] | None = None,
    body: str | None = None,
) -> dict[str, object]:
    issue: dict[str, object] = {
        "id": issue_id,
        "title": title,
        "status": status,
        "priority": priority,
        "created": created,
        "completed": completed,
        "assignee": assignee,
        "stage": stage,
        "implementer": implementer,
        "author": author,
        "worker": worker,
        "target_worker": target_worker,
        "origin": origin,
        "body": body if body is not None else f"# Issue #{issue_id}: {title}\n",
    }
    if depends_on is not None:
        issue["depends_on"] = depends_on
    if dependency_state is not None:
        issue["dependency_state"] = dependency_state
    if dependencies is not None:
        issue["dependencies"] = dependencies
    return issue
