"""truststack-task-dedupe — prevent duplicate task creation.

Tasks captured from email, chat, and meeting transcripts are fingerprinted by
normalized intent (title, due-window, assignee, project) and compared with a
similarity scorer; near-duplicates are detected before a new task is created.

Example::

    import asyncio
    from task_dedupe import DedupeEngine, Task

    async def main() -> None:
        engine = DedupeEngine()
        first = await engine.check(Task(title="Send Q3 report to Dana", due="tomorrow"))
        assert not first.duplicate
        again = await engine.check(Task(title="send the q3 report to dana", due="tomorrow"))
        assert again.duplicate

    asyncio.run(main())
"""

from __future__ import annotations

from .engine import DedupeEngine
from .fingerprint import (
    FingerprintInputs,
    fingerprint_inputs,
    fingerprint_task,
    normalize_due,
    normalize_title,
)
from .models import DedupeResult, FingerprintParts, Task
from .similarity import (
    DifflibScorer,
    HashingEmbeddingScorer,
    RapidFuzzScorer,
    SequenceMatcherScorer,
    SimilarityScorer,
)
from .stores import (
    InMemoryTaskStore,
    PostgresTaskStore,
    RedisTaskStore,
    SqliteTaskStore,
    StoredTask,
    TaskStore,
)

__version__ = "0.1.0"

__all__ = [
    "DedupeEngine",
    "DedupeResult",
    "DifflibScorer",
    "FingerprintInputs",
    "FingerprintParts",
    "HashingEmbeddingScorer",
    "InMemoryTaskStore",
    "PostgresTaskStore",
    "RapidFuzzScorer",
    "RedisTaskStore",
    "SequenceMatcherScorer",
    "SimilarityScorer",
    "SqliteTaskStore",
    "StoredTask",
    "Task",
    "TaskStore",
    "fingerprint_inputs",
    "fingerprint_task",
    "normalize_due",
    "normalize_title",
]
