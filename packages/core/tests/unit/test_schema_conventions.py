"""Static rules over the ORM metadata that keep the schema honest without a database."""

import pytest
from sqlalchemy import DateTime, PrimaryKeyConstraint, Table, UniqueConstraint

from doculens.infrastructure.persistence.models import Base

pytestmark = pytest.mark.unit


def _leading_indexed_columns(table: Table) -> set[str]:
    # Expression indexes (full-text search) have no columns and index no foreign key.
    leading = {next(iter(index.columns)).name for index in table.indexes if index.columns}
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint | PrimaryKeyConstraint) and constraint.columns:
            leading.add(next(iter(constraint.columns)).name)
    return leading


@pytest.mark.parametrize("table", Base.metadata.sorted_tables, ids=lambda table: table.name)
def test_every_foreign_key_column_leads_an_index(table: Table) -> None:
    """Referential actions (CASCADE, SET NULL) and owner lookups must never scan a table."""
    leading = _leading_indexed_columns(table)
    unindexed = sorted(fk.parent.name for fk in table.foreign_keys if fk.parent.name not in leading)

    assert unindexed == [], f"{table.name}: foreign keys without a leading index: {unindexed}"


@pytest.mark.parametrize("table", Base.metadata.sorted_tables, ids=lambda table: table.name)
def test_timestamps_are_timezone_aware(table: Table) -> None:
    naive = [
        column.name
        for column in table.columns
        if isinstance(column.type, DateTime) and not column.type.timezone
    ]

    assert naive == []


def test_no_orm_relationships_are_declared() -> None:
    """Reads go through explicit repository queries: no lazy loading, no ORM-side cascades."""
    for mapper in Base.registry.mappers:
        assert list(mapper.relationships) == [], mapper.class_.__name__
