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
_API_UNAVAILABLE_MESSAGE = (
    "Negotiation API endpoints are not available until proposal #112 is adopted."
)


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

    def set_status(self, thread_id: str, status: ThreadStatus) -> None:
        """Set the status for a negotiation thread."""

    def get_status(self, thread_id: str) -> ThreadStatus:
        """Return the status for a negotiation thread."""


class MockNegotiationStore:
    """In-memory negotiation store with JSON persistence for local runs."""

    def __init__(self, persistence_path: str | Path | None = DEFAULT_NEGOTIATION_PATH) -> None:
        self.persistence_path = Path(persistence_path) if persistence_path is not None else None
        self._threads: dict[str, list[NegotiationEntry]] = {}
        self._statuses: dict[str, ThreadStatus] = {}
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

    def set_status(self, thread_id: str, status: ThreadStatus) -> None:
        self._ensure_thread(thread_id)
        self._statuses[thread_id] = _coerce_status(status)
        self._persist()

    def get_status(self, thread_id: str) -> ThreadStatus:
        self._ensure_thread(thread_id)
        return self._statuses[thread_id]

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

    def _persist(self) -> None:
        if self.persistence_path is None:
            return
        self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "next_thread_id": self._next_thread_id,
            "next_entry_id": self._next_entry_id,
            "statuses": {key: status.value for key, status in self._statuses.items()},
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
    """Placeholder for proposal #112-backed negotiation endpoints."""

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
        raise _api_unavailable()

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
        raise _api_unavailable()

    def get_thread(self, thread_id: str) -> list[NegotiationEntry]:
        raise _api_unavailable()

    def set_status(self, thread_id: str, status: ThreadStatus) -> None:
        raise _api_unavailable()

    def get_status(self, thread_id: str) -> ThreadStatus:
        raise _api_unavailable()


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


def _coerce_verdict(value: object) -> Verdict:
    try:
        return value if isinstance(value, Verdict) else Verdict(str(value))
    except ValueError as exc:
        raise ValueError(f"Invalid verdict: {value}") from exc


def _validate_entry_input(side: str, verdict: object) -> None:
    if not side or not is_valid_workflow_token(side):
        raise ValueError(f"Invalid side token: {side}")
    _coerce_verdict(verdict)


def _coerce_status(value: object) -> ThreadStatus:
    try:
        return value if isinstance(value, ThreadStatus) else ThreadStatus(str(value))
    except ValueError as exc:
        raise ValueError(f"Invalid thread status: {value}") from exc


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _api_unavailable() -> WorkflowError:
    return WorkflowError(_API_UNAVAILABLE_MESSAGE, code="negotiation_api_unavailable")
