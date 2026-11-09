from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.db.models import Base


def table_counts(session: Session) -> dict[str, dict[str, int]]:
    """Row counts for every table: `{table: {status: count}}`, or `{table: {"total": count}}`
    for tables without a `status` column. Tables are listed even when empty."""
    counts: dict[str, dict[str, int]] = {}
    for name, table in sorted(Base.metadata.tables.items()):
        if "status" in table.columns:
            status = table.columns["status"]
            rows = session.execute(select(status, func.count()).group_by(status)).all()
            counts[name] = {str(row_status): count for row_status, count in rows}
        else:
            total = session.execute(select(func.count()).select_from(table)).scalar_one()
            counts[name] = {"total": total}
    return counts
