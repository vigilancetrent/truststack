"""The deduplication engine — a Trust Stack component."""

from __future__ import annotations

from uuid import uuid4

from truststack.core import BaseTrustComponent, HealthState, HealthStatus
from truststack.events import EventBus, TrustEvent
from truststack.logging import get_logger
from truststack.observability import traced

from .fingerprint import fingerprint_inputs
from .models import DedupeResult, FingerprintParts, Task
from .similarity import DifflibScorer, SimilarityScorer
from .stores import InMemoryTaskStore, TaskStore

log = get_logger(__name__)


class DedupeEngine(BaseTrustComponent):
    """Detect and suppress duplicate tasks via fingerprinting + similarity.

    A new task is fingerprinted and compared against every stored task. If the
    best similarity meets ``threshold`` the task is reported as a duplicate (and
    not stored); otherwise it is stored and reported as new. A
    ``task.duplicate_detected`` :class:`TrustEvent` is emitted on each hit.
    """

    component_name = "task-dedupe"
    component_version = "0.1.0"

    def __init__(
        self,
        store: TaskStore | None = None,
        threshold: float = 0.85,
        event_bus: EventBus | None = None,
        scorer: SimilarityScorer | None = None,
    ) -> None:
        super().__init__()
        if not 0.0 <= threshold <= 1.0:
            msg = f"threshold must be in [0, 1], got {threshold}"
            raise ValueError(msg)
        self._store: TaskStore = store if store is not None else InMemoryTaskStore()
        self._threshold = threshold
        self._event_bus = event_bus
        self._scorer: SimilarityScorer = scorer if scorer is not None else DifflibScorer()

    @property
    def scorer(self) -> SimilarityScorer:
        return self._scorer

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def store(self) -> TaskStore:
        return self._store

    @staticmethod
    def _coerce(task: Task | dict[str, object]) -> Task:
        if isinstance(task, Task):
            return task
        return Task.model_validate(task)

    @traced("task_dedupe.check")
    async def check(self, task: Task | dict[str, object]) -> DedupeResult:
        """Check ``task`` against the store; store it only if it is new."""
        candidate = self._coerce(task)
        inputs = fingerprint_inputs(candidate)
        fingerprint = inputs.to_fingerprint()
        parts = FingerprintParts(
            title=inputs.title,
            due=inputs.due,
            assignee=inputs.assignee,
            project=inputs.project,
        )
        self.registry.increment("checks_total")

        best_score = 0.0
        best_id: str | None = None
        for record in await self._store.all():
            # An identical fingerprint is a guaranteed duplicate (score 1.0).
            if record.fingerprint == fingerprint:
                best_score = 1.0
                best_id = record.id
                break
            score = self._scorer.score(candidate, record.task)
            if score > best_score:
                best_score = score
                best_id = record.id

        self.registry.set_gauge("last_best_score", best_score)

        if best_id is not None and best_score >= self._threshold:
            self.registry.increment("duplicates_detected")
            log.info(
                "task.duplicate_detected",
                existing_task_id=best_id,
                score=best_score,
                fingerprint=fingerprint,
            )
            await self._emit_duplicate(candidate, best_id, best_score, fingerprint)
            return DedupeResult(
                duplicate=True,
                existing_task_id=best_id,
                score=best_score,
                fingerprint=fingerprint,
                fingerprint_inputs=parts,
            )

        new_id = uuid4().hex
        await self._store.add(new_id, fingerprint, candidate)
        self.registry.increment("tasks_stored")
        log.info("task.stored", task_id=new_id, score=best_score, fingerprint=fingerprint)
        return DedupeResult(
            duplicate=False,
            existing_task_id=None,
            score=best_score,
            fingerprint=fingerprint,
            fingerprint_inputs=parts,
        )

    async def _emit_duplicate(
        self, task: Task, existing_task_id: str, score: float, fingerprint: str
    ) -> None:
        if self._event_bus is None:
            return
        await self._event_bus.publish(
            TrustEvent(
                name="task.duplicate_detected",
                component=self.component_name,
                data={
                    "existing_task_id": existing_task_id,
                    "score": score,
                    "fingerprint": fingerprint,
                    "title": task.title,
                },
            )
        )

    async def _check_health(self) -> HealthStatus:
        try:
            count = len(await self._store.all())
        except Exception as exc:
            return HealthStatus(
                component=self.component_name,
                state=HealthState.UNHEALTHY,
                detail=f"store unavailable: {exc}",
            )
        return HealthStatus(
            component=self.component_name,
            state=HealthState.HEALTHY,
            detail=f"{count} task(s) tracked",
        )


__all__ = ["DedupeEngine"]
