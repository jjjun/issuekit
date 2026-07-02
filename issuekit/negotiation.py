"""Negotiation thread data model and storage abstraction."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
import json
from pathlib import Path
from typing import Any, Protocol

from issuekit.client import IssuekitClient
from issuekit.config import IssuekitConfig
from issuekit.core import is_valid_workflow_token
from issuekit.workflow import WorkflowError


DEFAULT_NEGOTIATION_PATH = Path(".agent-runs") / "negotiations" / "mock.json"
MAX_CONTRACT_LENGTH = 100000


class Verdict(StrEnum):
    propose = "propose"
    counter = "counter"
    agree = "agree"
    blocked = "blocked"


class ThreadStatus(StrEnum):
    negotiating = "negotiating"
    agreed = "agreed"
    blocked = "blocked"


@dataclass(frozen=True)
class NegotiationEntry:
    thread_id: str
    side: str
    verdict: Verdict
    contract: str | None
    title: str
    body: str
    origin: str
    created: str
    id: int | None = None

    def __post_init__(self) -> None:
        if not self.thread_id:
            raise ValueError("thread_id is required")
        if not self.side or not is_valid_workflow_token(self.side):
            raise ValueError(f"Invalid side token: {self.side}")
        object.__setattr__(self, "verdict", _coerce_verdict(self.verdict))


@dataclass(frozen=True)
class NegotiationIssueRefs:
    backend_issue_ref: str
    frontend_issue_ref: str

    def __post_init__(self) -> None:
        if not self.backend_issue_ref.strip():
            raise ValueError("backend_issue_ref is required")
        if not self.frontend_issue_ref.strip():
            raise ValueError("frontend_issue_ref is required")

    def to_dict(self) -> dict[str, str]:
        return {
            "backend_issue_ref": self.backend_issue_ref,
            "frontend_issue_ref": self.frontend_issue_ref,
        }


@dataclass(frozen=True)
class NegotiationThreadSummary:
    thread_id: str
    status: ThreadStatus
    agreed_contract: str | None = None
    issue_refs: NegotiationIssueRefs | None = None
    updated: str = ""


class NegotiationStore(Protocol):
    def create_thread(
        self,
        *,
        side: str,
        verdict: Verdict,
        title: str,
        body: str,
        origin: str,
        contract: str | None = None,
    ) -> NegotiationEntry:
        """Create a negotiation thread and return its first entry."""

    def append_entry(
        self,
        thread_id: str,
        *,
        side: str,
        verdict: Verdict,
        title: str,
        body: str,
        origin: str,
        contract: str | None = None,
    ) -> NegotiationEntry:
        """Append one entry to an existing negotiation thread."""

    def get_thread(self, thread_id: str) -> list[NegotiationEntry]:
        """Return a thread's entries in stable ascending order."""

    def list_threads(self, *, status: ThreadStatus | None = None) -> list[NegotiationThreadSummary]:
        """Return known negotiation threads in stable ascending order."""

    def set_status(
        self,
        thread_id: str,
        status: ThreadStatus,
        *,
        agreed_contract: str | None = None,
    ) -> None:
        """Set the status for a negotiation thread."""

    def get_status(self, thread_id: str) -> ThreadStatus:
        """Return the status for a negotiation thread."""

    def get_agreed_contract(self, thread_id: str) -> str | None:
        """Return the agreed contract frozen on the thread, if any."""

    def get_issue_refs(self, thread_id: str) -> NegotiationIssueRefs | None:
        """Return implementation issue refs recorded on a finalized thread."""

    def set_issue_refs(self, thread_id: str, refs: NegotiationIssueRefs) -> None:
        """Record implementation issue refs for a finalized thread."""


class MockNegotiationStore:
    """In-memory negotiation store with JSON persistence for local runs."""

    def __init__(self, persistence_path: str | Path | None = DEFAULT_NEGOTIATION_PATH) -> None:
        self.persistence_path = Path(persistence_path) if persistence_path is not None else None
        self._threads: dict[str, list[NegotiationEntry]] = {}
        self._statuses: dict[str, ThreadStatus] = {}
        self._agreed_contracts: dict[str, str | None] = {}
        self._issue_refs: dict[str, NegotiationIssueRefs] = {}
        self._next_thread_id = 1
        self._next_entry_id = 1
        self._load()

    def create_thread(
        self,
        *,
        side: str,
        verdict: Verdict,
        title: str,
        body: str,
        origin: str,
        contract: str | None = None,
    ) -> NegotiationEntry:
        _validate_entry_input(side, verdict)
        _validate_contract(contract)
        thread_id = str(self._next_thread_id)
        self._next_thread_id += 1
        entry = self._make_entry(
            thread_id,
            side=side,
            verdict=verdict,
            title=title,
            body=body,
            origin=origin,
            contract=contract,
        )
        self._threads[thread_id] = [entry]
        self._statuses[thread_id] = ThreadStatus.negotiating
        self._agreed_contracts[thread_id] = None
        self._issue_refs.pop(thread_id, None)
        self._persist()
        return entry

    def append_entry(
        self,
        thread_id: str,
        *,
        side: str,
        verdict: Verdict,
        title: str,
        body: str,
        origin: str,
        contract: str | None = None,
    ) -> NegotiationEntry:
        self._ensure_thread(thread_id)
        _validate_entry_input(side, verdict)
        _validate_contract(contract)
        self._ensure_negotiating(thread_id)
        self._ensure_unique_origin(thread_id, origin)
        entry = self._make_entry(
            thread_id,
            side=side,
            verdict=verdict,
            title=title,
            body=body,
            origin=origin,
            contract=contract,
        )
        self._threads[thread_id].append(entry)
        self._persist()
        return entry

    def get_thread(self, thread_id: str) -> list[NegotiationEntry]:
        self._ensure_thread(thread_id)
        return sorted(
            self._threads[thread_id],
            key=lambda entry: (
                entry.id is None,
                entry.id if entry.id is not None else 0,
                entry.created,
            ),
        )

    def list_threads(self, *, status: ThreadStatus | None = None) -> list[NegotiationThreadSummary]:
        requested_status = _coerce_status(status) if status is not None else None
        summaries = [
            NegotiationThreadSummary(
                thread_id=thread_id,
                status=thread_status,
                agreed_contract=self._agreed_contracts.get(thread_id),
                issue_refs=self._issue_refs.get(thread_id),
                updated=max((entry.created for entry in self._threads[thread_id]), default=""),
            )
            for thread_id, thread_status in self._statuses.items()
            if requested_status is None or thread_status is requested_status
        ]
        return sorted(summaries, key=lambda summary: _thread_sort_key(summary.thread_id))

    def set_status(
        self,
        thread_id: str,
        status: ThreadStatus,
        *,
        agreed_contract: str | None = None,
    ) -> None:
        self._ensure_thread(thread_id)
        next_status = _coerce_status(status)
        _validate_contract(agreed_contract)
        current_status = self._statuses[thread_id]
        if current_status is not ThreadStatus.negotiating:
            raise WorkflowError(
                f"Negotiation thread {thread_id} is already {current_status.value}.",
                code="invalid_transition",
            )
        if next_status is ThreadStatus.agreed:
            self._agreed_contracts[thread_id] = agreed_contract or _latest_agree_contract(
                self._threads[thread_id]
            )
        elif agreed_contract is not None:
            raise WorkflowError(
                "agreed_contract can only be set when status is agreed.",
                code="invalid_transition",
            )
        self._statuses[thread_id] = next_status
        self._persist()

    def get_status(self, thread_id: str) -> ThreadStatus:
        self._ensure_thread(thread_id)
        return self._statuses[thread_id]

    def get_agreed_contract(self, thread_id: str) -> str | None:
        self._ensure_thread(thread_id)
        return self._agreed_contracts.get(thread_id)

    def get_issue_refs(self, thread_id: str) -> NegotiationIssueRefs | None:
        self._ensure_thread(thread_id)
        return self._issue_refs.get(thread_id)

    def set_issue_refs(self, thread_id: str, refs: NegotiationIssueRefs) -> None:
        self._ensure_thread(thread_id)
        if self._statuses[thread_id] is not ThreadStatus.agreed:
            raise WorkflowError(
                f"Negotiation thread {thread_id} is {self._statuses[thread_id].value}, not agreed.",
                code="invalid_transition",
            )
        if thread_id in self._issue_refs and self._issue_refs[thread_id] != refs:
            raise WorkflowError(
                f"Negotiation thread {thread_id} already has implementation issue refs.",
                code="invalid_transition",
            )
        self._issue_refs[thread_id] = refs
        self._persist()

    def _make_entry(
        self,
        thread_id: str,
        *,
        side: str,
        verdict: Verdict,
        title: str,
        body: str,
        origin: str,
        contract: str | None,
    ) -> NegotiationEntry:
        entry_id = self._next_entry_id
        self._next_entry_id += 1
        return NegotiationEntry(
            thread_id=thread_id,
            side=side,
            verdict=verdict,
            contract=contract,
            title=title,
            body=body,
            origin=origin,
            created=date.today().isoformat(),
            id=entry_id,
        )

    def _ensure_thread(self, thread_id: str) -> None:
        if thread_id not in self._threads:
            raise WorkflowError(
                f"Negotiation thread {thread_id} was not found.",
                code="not_found",
            )

    def _ensure_negotiating(self, thread_id: str) -> None:
        status = self._statuses[thread_id]
        if status is not ThreadStatus.negotiating:
            raise WorkflowError(
                f"Negotiation thread {thread_id} is {status.value} and cannot be modified.",
                code="invalid_transition",
            )

    def _ensure_unique_origin(self, thread_id: str, origin: str) -> None:
        if any(entry.origin == origin for entry in self._threads[thread_id]):
            raise WorkflowError(
                f"Negotiation thread {thread_id} already has origin {origin}.",
                code="duplicate_origin",
            )

    def _load(self) -> None:
        if self.persistence_path is None or not self.persistence_path.exists():
            return
        raw = json.loads(self.persistence_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise WorkflowError("Negotiation persistence file was not a JSON object.")
        self._next_thread_id = int(raw.get("next_thread_id", 1))
        self._next_entry_id = int(raw.get("next_entry_id", 1))
        raw_threads = raw.get("threads", {})
        if not isinstance(raw_threads, dict):
            raise WorkflowError("Negotiation persistence threads was not a JSON object.")
        self._threads = {
            str(thread_id): [_entry_from_json(entry) for entry in entries]
            for thread_id, entries in raw_threads.items()
            if isinstance(entries, list)
        }
        raw_statuses = raw.get("statuses", {})
        if not isinstance(raw_statuses, dict):
            raise WorkflowError("Negotiation persistence statuses was not a JSON object.")
        self._statuses = {
            str(thread_id): _coerce_status(status)
            for thread_id, status in raw_statuses.items()
            if str(thread_id) in self._threads
        }
        for thread_id in self._threads:
            self._statuses.setdefault(thread_id, ThreadStatus.negotiating)
        raw_agreed_contracts = raw.get("agreed_contracts", {})
        if not isinstance(raw_agreed_contracts, dict):
            raise WorkflowError("Negotiation persistence agreed_contracts was not a JSON object.")
        self._agreed_contracts = {
            str(thread_id): _optional_string(contract)
            for thread_id, contract in raw_agreed_contracts.items()
            if str(thread_id) in self._threads
        }
        for thread_id in self._threads:
            self._agreed_contracts.setdefault(thread_id, None)
        raw_issue_refs = raw.get("issue_refs", {})
        if not isinstance(raw_issue_refs, dict):
            raise WorkflowError("Negotiation persistence issue_refs was not a JSON object.")
        self._issue_refs = {
            str(thread_id): _issue_refs_from_json(refs)
            for thread_id, refs in raw_issue_refs.items()
            if str(thread_id) in self._threads
        }

    def _persist(self) -> None:
        if self.persistence_path is None:
            return
        self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "next_thread_id": self._next_thread_id,
            "next_entry_id": self._next_entry_id,
            "statuses": {key: status.value for key, status in self._statuses.items()},
            "agreed_contracts": self._agreed_contracts,
            "issue_refs": {
                thread_id: refs.to_dict() for thread_id, refs in self._issue_refs.items()
            },
            "threads": {
                thread_id: [_entry_to_json(entry) for entry in entries]
                for thread_id, entries in self._threads.items()
            },
        }
        self.persistence_path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class ApiNegotiationStore:
    """Negotiation store backed by mine-py's proposal thread endpoints."""

    def __init__(
        self,
        config: IssuekitConfig,
        client: IssuekitClient | None = None,
    ) -> None:
        self.config = config
        self.client = client or IssuekitClient(
            config.api_url,
            project=config.project,
            timeout=config.api_timeout,
        )

    def create_thread(
        self,
        *,
        side: str,
        verdict: Verdict,
        title: str,
        body: str,
        origin: str,
        contract: str | None = None,
    ) -> NegotiationEntry:
        _validate_entry_input(side, verdict)
        _validate_contract(contract)
        try:
            proposal = self.client.create_proposal(
                origin=origin,
                title=title,
                body=body,
                side=side,
                verdict=_coerce_verdict(verdict).value,
                contract=contract,
            )
        except WorkflowError as exc:
            raise _with_negotiation_context(exc, origin, self.config.project) from exc
        entry = _entry_from_api(proposal)
        _ensure_idempotent_entry(
            entry,
            origin=origin,
            side=side,
            verdict=_coerce_verdict(verdict),
            title=title,
            body=body,
            contract=contract,
            target_project=self.config.project,
        )
        return entry

    def append_entry(
        self,
        thread_id: str,
        *,
        side: str,
        verdict: Verdict,
        title: str,
        body: str,
        origin: str,
        contract: str | None = None,
    ) -> NegotiationEntry:
        _validate_entry_input(side, verdict)
        _validate_contract(contract)
        entries = self.get_thread(thread_id)
        if not entries:
            raise WorkflowError(
                f"Negotiation thread {thread_id} has no entries.",
                code="invalid_response",
            )
        last_entry_id = entries[-1].id
        if last_entry_id is None:
            raise WorkflowError(
                f"Negotiation thread {thread_id} last entry has no id.",
                code="invalid_response",
            )
        try:
            proposal = self.client.reply_proposal(
                last_entry_id,
                origin=origin,
                title=title,
                body=body,
                side=side,
                verdict=_coerce_verdict(verdict).value,
                contract=contract,
            )
        except WorkflowError as exc:
            raise _with_negotiation_context(exc, origin, self.config.project) from exc
        return _entry_from_api(proposal)

    def get_thread(self, thread_id: str) -> list[NegotiationEntry]:
        payload = self.client.get_thread(_api_thread_id(thread_id))
        items = payload.get("items")
        if not isinstance(items, list):
            raise WorkflowError(
                "Proposal thread response items was not a JSON array.",
                code="invalid_response",
            )
        return sorted(
            [_entry_from_api(item) for item in items],
            key=lambda entry: (
                entry.id is None,
                entry.id if entry.id is not None else 0,
                entry.created,
            ),
        )

    def list_threads(self, *, status: ThreadStatus | None = None) -> list[NegotiationThreadSummary]:
        requested_status = _coerce_status(status).value if status is not None else None
        return [
            _thread_summary_from_api(thread)
            for thread in self.client.list_threads(status=requested_status)
        ]

    def set_status(
        self,
        thread_id: str,
        status: ThreadStatus,
        *,
        agreed_contract: str | None = None,
    ) -> None:
        _validate_contract(agreed_contract)
        next_status = _coerce_status(status)
        if next_status is ThreadStatus.agreed and agreed_contract is None:
            agreed_contract = _latest_agree_contract(self.get_thread(thread_id))
        self.client.patch_thread(
            _api_thread_id(thread_id),
            status=next_status.value,
            agreed_contract=agreed_contract,
        )

    def get_status(self, thread_id: str) -> ThreadStatus:
        payload = self.client.get_thread(_api_thread_id(thread_id))
        return _status_from_api(payload)

    def get_agreed_contract(self, thread_id: str) -> str | None:
        payload = self.client.get_thread(_api_thread_id(thread_id))
        contract = payload.get("agreed_contract")
        if contract is not None and not isinstance(contract, str):
            raise WorkflowError(
                "Proposal thread agreed_contract was not a string or null.",
                code="invalid_response",
            )
        return contract

    def get_issue_refs(self, thread_id: str) -> NegotiationIssueRefs | None:
        payload = self.client.get_thread(_api_thread_id(thread_id))
        return _issue_refs_from_api(payload, require_supported=True)

    def set_issue_refs(self, thread_id: str, refs: NegotiationIssueRefs) -> None:
        payload = self.client.patch_thread(
            _api_thread_id(thread_id),
            backend_issue_ref=refs.backend_issue_ref,
            frontend_issue_ref=refs.frontend_issue_ref,
        )
        stored_refs = _issue_refs_from_api(payload, require_supported=True)
        if stored_refs != refs:
            raise WorkflowError(
                "Proposal thread response did not confirm the requested issue refs.",
                code="server_schema_drift",
            )


def get_negotiation_store(
    config: IssuekitConfig,
    *,
    use_mock: bool,
) -> NegotiationStore:
    if use_mock:
        return MockNegotiationStore()
    if not config.api_url:
        raise WorkflowError(
            "API negotiation store requires api_url. Set api_url in "
            "issuekit.toml/[tool.issuekit] or ISSUEKIT_API_URL.",
            code="missing_api_url",
        )
    return ApiNegotiationStore(config)


def _with_negotiation_context(
    exc: WorkflowError,
    origin: str,
    target_project: str,
) -> WorkflowError:
    return WorkflowError(
        f"{exc} (negotiation origin {origin}, target project {target_project})",
        code=exc.code,
    )


def _ensure_idempotent_entry(
    entry: NegotiationEntry,
    *,
    origin: str,
    side: str,
    verdict: Verdict,
    title: str,
    body: str,
    contract: str | None,
    target_project: str,
) -> None:
    """Accept same-origin responses only as exact idempotent retries."""
    if entry.origin != origin:
        return
    mismatched = [
        name
        for name, requested, returned in (
            ("side", side, entry.side),
            ("verdict", verdict, entry.verdict),
            ("title", title, entry.title),
            ("body", body, entry.body),
            ("contract", contract, entry.contract),
        )
        if requested != returned
    ]
    if mismatched:
        raise WorkflowError(
            f"Target project {target_project} returned existing proposal "
            f"#{entry.id} for origin {origin} with different "
            f"{', '.join(mismatched)}; a pending entry already uses this origin. "
            "Adopt or discard it before reopening a negotiation with this origin.",
            code="duplicate_origin",
        )


def _entry_to_json(entry: NegotiationEntry) -> dict[str, Any]:
    data = asdict(entry)
    data["verdict"] = entry.verdict.value
    return data


def _entry_from_json(raw: Any) -> NegotiationEntry:
    if not isinstance(raw, dict):
        raise WorkflowError("Negotiation entry persistence item was not a JSON object.")
    return NegotiationEntry(
        thread_id=str(raw.get("thread_id", "")),
        side=str(raw.get("side", "")),
        verdict=_coerce_verdict(raw.get("verdict")),
        contract=raw.get("contract") if raw.get("contract") is not None else None,
        title=str(raw.get("title", "")),
        body=str(raw.get("body", "")),
        origin=str(raw.get("origin", "")),
        created=str(raw.get("created", "")),
        id=_optional_int(raw.get("id")),
    )


def _issue_refs_from_json(raw: Any) -> NegotiationIssueRefs:
    if not isinstance(raw, dict):
        raise WorkflowError("Negotiation issue_refs item was not a JSON object.")
    try:
        return NegotiationIssueRefs(
            backend_issue_ref=str(raw["backend_issue_ref"]),
            frontend_issue_ref=str(raw["frontend_issue_ref"]),
        )
    except KeyError as exc:
        raise WorkflowError(
            f"Negotiation issue_refs item missing {exc.args[0]}.",
            code="invalid_response",
        ) from exc


def _thread_sort_key(thread_id: str) -> tuple[int, int | str]:
    try:
        return (0, int(thread_id))
    except (TypeError, ValueError):
        return (1, str(thread_id))


def _coerce_verdict(value: object) -> Verdict:
    try:
        return value if isinstance(value, Verdict) else Verdict(str(value))
    except ValueError as exc:
        raise ValueError(f"Invalid verdict: {value}") from exc


def _validate_entry_input(side: str, verdict: object) -> None:
    if not side or not is_valid_workflow_token(side):
        raise ValueError(f"Invalid side token: {side}")
    _coerce_verdict(verdict)


def _validate_contract(contract: str | None) -> None:
    if contract is not None and len(contract) > MAX_CONTRACT_LENGTH:
        raise WorkflowError(
            f"Negotiation contract exceeds {MAX_CONTRACT_LENGTH} characters.",
            code="invalid_value",
        )


def _coerce_status(value: object) -> ThreadStatus:
    try:
        return value if isinstance(value, ThreadStatus) else ThreadStatus(str(value))
    except ValueError as exc:
        raise ValueError(f"Invalid thread status: {value}") from exc


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkflowError("Negotiation persistence value was not a string or null.")
    _validate_contract(value)
    return value


def _latest_agree_contract(entries: list[NegotiationEntry]) -> str | None:
    for entry in reversed(entries):
        if entry.verdict is Verdict.agree and entry.contract is not None:
            return entry.contract
    return None


def _api_thread_id(thread_id: str) -> int:
    try:
        return int(thread_id)
    except (TypeError, ValueError) as exc:
        raise WorkflowError(
            f"Negotiation thread id {thread_id!r} is not an API thread id.",
            code="invalid_value",
        ) from exc


def _entry_from_api(raw: Any) -> NegotiationEntry:
    if not isinstance(raw, dict):
        raise WorkflowError("Proposal response was not a JSON object.", code="invalid_response")
    try:
        thread_id = raw["thread_id"]
        side = raw["side"]
        verdict = raw["verdict"]
        title = raw["title"]
        body = raw["body"]
        origin = raw["origin"]
    except KeyError as exc:
        raise WorkflowError(
            f"Proposal response missing {exc.args[0]}.",
            code="invalid_response",
        ) from exc
    contract = raw.get("contract")
    if not isinstance(thread_id, int):
        raise WorkflowError("Proposal response thread_id was not an integer.", code="invalid_response")
    if not isinstance(side, str) or not isinstance(verdict, str):
        raise WorkflowError("Proposal response negotiation fields were invalid.", code="invalid_response")
    if contract is not None and not isinstance(contract, str):
        raise WorkflowError("Proposal response contract was not a string or null.", code="invalid_response")
    if not isinstance(title, str) or not isinstance(body, str) or not isinstance(origin, str):
        raise WorkflowError("Proposal response text fields were invalid.", code="invalid_response")
    created = raw.get("created_at") or raw.get("created") or ""
    if not isinstance(created, str):
        raise WorkflowError("Proposal response created timestamp was invalid.", code="invalid_response")
    try:
        return NegotiationEntry(
            thread_id=str(thread_id),
            side=side,
            verdict=verdict,
            contract=contract,
            title=title,
            body=body,
            origin=origin,
            created=created,
            id=_optional_int(raw.get("id")),
        )
    except (TypeError, ValueError) as exc:
        raise WorkflowError(str(exc), code="invalid_response") from exc


def _issue_refs_from_api(
    raw: Any,
    *,
    require_supported: bool = False,
) -> NegotiationIssueRefs | None:
    if not isinstance(raw, dict):
        raise WorkflowError("Proposal thread response was not a JSON object.", code="invalid_response")
    if isinstance(raw.get("issue_refs"), dict):
        nested = raw["issue_refs"]
    elif isinstance(raw.get("adopted_issue_refs"), dict):
        nested = raw["adopted_issue_refs"]
    else:
        nested = None
    if isinstance(nested, dict):
        backend_ref = nested.get("backend_issue_ref") or nested.get("backend")
        frontend_ref = nested.get("frontend_issue_ref") or nested.get("frontend")
        supported = True
    else:
        backend_ref = raw.get("backend_issue_ref")
        frontend_ref = raw.get("frontend_issue_ref")
        supported = "backend_issue_ref" in raw and "frontend_issue_ref" in raw
    if require_supported and not supported:
        raise WorkflowError(
            "Proposal thread response did not include issue-ref fields; upgrade the "
            "mine-py API server before finalizing negotiation threads.",
            code="server_schema_drift",
        )
    if backend_ref is None and frontend_ref is None:
        return None
    if not isinstance(backend_ref, str) or not isinstance(frontend_ref, str):
        raise WorkflowError(
            "Proposal thread issue refs were incomplete or invalid.",
            code="invalid_response",
        )
    try:
        return NegotiationIssueRefs(
            backend_issue_ref=backend_ref,
            frontend_issue_ref=frontend_ref,
        )
    except ValueError as exc:
        raise WorkflowError(str(exc), code="invalid_response") from exc


def _thread_summary_from_api(raw: Any) -> NegotiationThreadSummary:
    if not isinstance(raw, dict):
        raise WorkflowError("Proposal thread response was not a JSON object.", code="invalid_response")
    try:
        thread_id = raw["id"]
    except KeyError as exc:
        raise WorkflowError(
            f"Proposal thread response missing {exc.args[0]}.",
            code="invalid_response",
        ) from exc
    try:
        status = _status_from_api(raw)
    except WorkflowError:
        raise
    contract = raw.get("agreed_contract")
    if contract is not None and not isinstance(contract, str):
        raise WorkflowError(
            "Proposal thread agreed_contract was not a string or null.",
            code="invalid_response",
        )
    updated = raw.get("updated_at") or raw.get("updated") or ""
    if not isinstance(updated, str):
        raise WorkflowError("Proposal thread updated timestamp was invalid.", code="invalid_response")
    return NegotiationThreadSummary(
        thread_id=str(thread_id),
        status=status,
        agreed_contract=contract,
        issue_refs=_issue_refs_from_api(raw),
        updated=updated,
    )


def _status_from_api(raw: Any) -> ThreadStatus:
    if not isinstance(raw, dict):
        raise WorkflowError("Proposal thread response was not a JSON object.", code="invalid_response")
    try:
        return _coerce_status(raw.get("status"))
    except ValueError as exc:
        raise WorkflowError(str(exc), code="invalid_response") from exc
