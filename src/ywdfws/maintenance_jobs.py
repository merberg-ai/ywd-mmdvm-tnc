"""FM1.5a: bounded, session-scoped, read-only maintenance preflight jobs.

A browser may launch *only* a backup-readiness audit of on-disk metadata.
This module cannot operate on the HAT, trigger the backup CLI, run shell
commands, or communicate a privileged write opcode. In-memory only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import secrets
import threading
import time

from .broker import fetch as broker_inventory
from .inventory import inventory


MAX_HISTORY = 8
STAGES = 3


@dataclass
class Job:
    id: str
    owner: str
    status: str = "running"
    step: int = 0
    events: list[dict] = field(default_factory=list)
    result: dict | None = None
    started: float = field(default_factory=time.time)

    def public(self) -> dict:
        return {
            "id": self.id,
            "kind": "backup-readiness",
            "mode": "READ_ONLY_PREFLIGHT",
            "hardware_access": False,
            "firmware_written": False,
            "status": self.status,
            "steps_completed": self.step,
            "steps_total": STAGES,
            "events": list(self.events),
            "result": dict(self.result) if self.result else None,
        }


class JobConflict(ValueError):
    pass


class JobManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs: dict[str, Job] = {}

    def start(self, owner: str) -> dict:
        if not isinstance(owner, str) or not owner:
            raise ValueError("session required")
        with self.lock:
            if any(j.status == "running" for j in self.jobs.values()):
                raise JobConflict("A preflight is already running")
            while len(self.jobs) >= MAX_HISTORY:
                self.jobs.pop(next(iter(self.jobs)))
            ident = secrets.token_urlsafe(18)
            job = Job(id=ident, owner=owner)
            self.jobs[ident] = job
            self._event(job, "Preflight requested. No UART, GPIO or flash operations are available.")
            worker = threading.Thread(target=self._run, args=(ident,), daemon=True)
            worker.start()
            return job.public()

    def get(self, owner: str, ident: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(ident)
            if job is None or not secrets.compare_digest(job.owner, owner):
                return None
            return job.public()

    def _event(self, job: Job, message: str) -> None:
        job.events.append({"seq": len(job.events) + 1, "timestamp": int(time.time()), "message": message})

    def _run(self, ident: str) -> None:
        try:
            with self.lock:
                job = self.jobs[ident]
                self._event(job, "Inspecting pinned firmware target and local backup catalog.")
            # The unprivileged reader never opens the modem UART.
            try:
                report = broker_inventory()
            except (OSError, ValueError, TypeError):
                report = inventory()
                report["catalog_access"] = "unprivileged-local"
            with self.lock:
                job.step = 1
                self._event(job, "Qualified profile loaded; live HAT identity remains unprobed.")
                job.step = 2
                access = report.get("backup_access")
                # When the root-owned catalog is inaccessible, zero visible
                # entries is NOT evidence that zero backups exist.
                restricted = access == "restricted"
                count = None if restricted else report.get("backup_count")
                golden = (None if restricted else sum(
                    1 for entry in report.get("backups", [])
                    if entry.get("kind") == "golden_stock"
                    and entry.get("integrity") == "verified"
                    and entry.get("restore_qualified") is True))
                if restricted:
                    self._event(job, "Backup catalog restricted; backup counts are unknown.")
                else:
                    self._event(job, f"Backup catalog checked: {count} records, {golden} verified golden stock.")
                job.step = 3
                job.status = "limited" if access == "restricted" else "complete"
                job.result = {
                    "backup_access": access,
                    "backup_count": count,
                    "verified_golden_stock": golden,
                    "catalog_source": report.get("catalog_access", "unprivileged-local"),
                    "live_target_verified": False,
                    "hardware_backup_authorized": False,
                    "physical_backup_performed": False,
                    "next_action": ("Start the read-only inventory broker to see protected backups."
                                    if access == "restricted" else
                                    "Catalog review complete; hardware backup still requires a separate approved maintenance window."),
                }
                self._event(job, "Read-only preflight finished; NO hardware backup performed.")
        except Exception:
            with self.lock:
                job = self.jobs[ident]
                job.status = "failed"
                self._event(job, "Preflight could not read the local inventory. No hardware was accessed.")
