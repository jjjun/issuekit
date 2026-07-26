"""JSON-persisted mock negotiation store."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
from typing import Any

from issuekit.core import optional_int
from issuekit.negotiation.model import (
    DEFAULT_NEGOTIATION_PATH,
    NegotiationEntry,
    NegotiationIssueRefs,
    NegotiationThreadSummary,
    ThreadStatus,
    Verdict,
    coerce_status,
    coerce_verdict,
    latest_agree_contract,
    validate_contract,
    validate_entry_input,
)
from issuekit.workflow import WorkflowError


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

    def close(self) -> None:
        pass

    def __enter__(self) -> "MockNegotiationStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

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
        validate_entry_input(side, verdict)
        validate_contract(contract)
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
        validate_entry_input(side, verdict)
        validate_contract(contract)
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
        requested_status = coerce_status(status) if status is not None else None
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
        next_status = coerce_status(status)
        validate_contract(agreed_contract)
        current_status = self._statuses[thread_id]
        if current_status is not ThreadStatus.negotiating:
            raise WorkflowError(
                f"Negotiation thread {thread_id} is already {current_status.value}.",
                code="invalid_transition",
            )
        if next_status is ThreadStatus.agreed:
            self._agreed_contracts[thread_id] = agreed_contract or latest_agree_contract(
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
            str(thread_id): coerce_status(status)
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
        verdict=coerce_verdict(raw.get("verdict")),
        contract=raw.get("contract") if raw.get("contract") is not None else None,
        title=str(raw.get("title", "")),
        body=str(raw.get("body", "")),
        origin=str(raw.get("origin", "")),
        created=str(raw.get("created", "")),
        id=optional_int(raw.get("id")),
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


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkflowError("Negotiation persistence value was not a string or null.")
    validate_contract(value)
    return value
