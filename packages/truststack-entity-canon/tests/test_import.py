"""Batch import coverage: CSV files and iterables (added/merged/skipped)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from entity_canon import CanonicalEntity, Canonicalizer, ImportCounts


def _write_csv(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


async def test_import_csv_basic(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "entities.csv",
        "name,aliases\nAlice,Ally|Al\nBob,Bobby\nMaria,\n",
    )
    canon = Canonicalizer()
    counts = await canon.import_csv(csv_path)
    assert isinstance(counts, ImportCounts)
    assert counts.added == 3
    assert counts.skipped == 0

    entities = {e.name: e for e in await canon.all()}
    assert set(entities) == {"Alice", "Bob", "Maria"}
    assert entities["Alice"].aliases == ["Ally", "Al"]
    assert entities["Bob"].aliases == ["Bobby"]
    assert entities["Maria"].aliases == []


async def test_import_csv_skips_blank_name_rows(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "e.csv",
        "name,aliases\nValid,\n,OrphanAlias\n   ,\n",
    )
    canon = Canonicalizer()
    counts = await canon.import_csv(csv_path)
    assert counts.added == 1
    assert counts.skipped == 2


async def test_import_csv_merges_phonetic_duplicate(tmp_path: Path) -> None:
    canon = Canonicalizer()
    await canon.add("Jatin")
    csv_path = _write_csv(
        tmp_path / "e.csv",
        "name,aliases\nJhatin,JT\nMichael,\n",
    )
    counts = await canon.import_csv(csv_path)
    assert counts.merged == 1  # Jhatin folds into Jatin
    assert counts.added == 1  # Michael is new
    entities = await canon.all()
    assert len(entities) == 2


async def test_import_csv_custom_field_names(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "e.csv",
        "full_name,nicknames\nRobert,Bob|Rob\n",
    )
    canon = Canonicalizer()
    counts = await canon.import_csv(
        csv_path,
        name_field="full_name",
        alias_field="nicknames",
    )
    assert counts.added == 1
    entity = (await canon.all())[0]
    assert entity.name == "Robert"
    assert entity.aliases == ["Bob", "Rob"]


async def test_import_csv_custom_separator(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "e.csv",
        "name,aliases\nRobert,Bob;Rob\n",
    )
    canon = Canonicalizer()
    counts = await canon.import_csv(csv_path, alias_sep=";")
    assert counts.added == 1
    entity = (await canon.all())[0]
    assert entity.aliases == ["Bob", "Rob"]


async def test_import_csv_unicode(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "e.csv",
        "name,aliases\nJosé,Pepe\nRenée,\n",
    )
    canon = Canonicalizer()
    counts = await canon.import_csv(csv_path)
    assert counts.added == 2
    names = {e.name for e in await canon.all()}
    assert names == {"José", "Renée"}


async def test_bulk_import_iterable_generator() -> None:
    def gen() -> Iterator[CanonicalEntity | dict[str, object]]:
        yield CanonicalEntity(id="1", name="Alice")
        yield {"id": "2", "name": "Bob"}
        yield {"id": "3", "name": ""}  # skipped

    canon = Canonicalizer()
    counts = await canon.bulk_import(gen())
    assert counts.added == 2
    assert counts.skipped == 1


async def test_bulk_import_empty_iterable() -> None:
    canon = Canonicalizer()
    counts = await canon.bulk_import([])
    assert counts == ImportCounts()
    assert counts.total == 0


async def test_import_metrics_recorded(tmp_path: Path) -> None:
    canon = Canonicalizer()
    await canon.add("Jatin")
    csv_path = _write_csv(tmp_path / "e.csv", "name,aliases\nJhatin,\nZara,\n")
    await canon.import_csv(csv_path)
    m = await canon.metrics()
    assert m.counters.get("entities_merged", 0) == 1
