"""The exact days a release covers, shared by every stage after 34_fleet_summary.py.

34 freezes the included days into results.json. Later stages read archive
tables through day_view(), which filters on that list rather than on the
first/last bounds, so a day backfilled while a release is being built cannot
reach the explorer or the downloads without also being in aircraft.parquet.
"""

import json
from pathlib import Path


def included_days(rel: Path) -> list[str]:
    return json.loads((rel / "results.json").read_text())["window"]["included_days"]


def day_view(con, name: str, table_glob: Path, days: list[str]) -> None:
    """CREATE VIEW <name> over the parquet glob, restricted to the frozen days."""
    in_list = ", ".join(f"DATE '{d}'" for d in days)
    con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{table_glob}') WHERE day IN ({in_list})")
