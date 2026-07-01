from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.data.sportmonks_client import get_fixtures_between
from src.data.sportmonks_ingestion import INTERNATIONAL_LEAGUES


@dataclass
class RefreshPlanItem:
    fixture_id: int
    raw_fixture: dict[str, Any]


def plan_upcoming_refreshes(window_hours: int = 48) -> list[RefreshPlanItem]:
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=window_hours)
    try:
        fixtures = get_fixtures_between(
            now,
            window_end,
            league_ids=list(INTERNATIONAL_LEAGUES.keys()),
            includes="participants;league",
        )
    except Exception:
        return []
    return [RefreshPlanItem(fixture_id=item["id"], raw_fixture=item) for item in fixtures]
