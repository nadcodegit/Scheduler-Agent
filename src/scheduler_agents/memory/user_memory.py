from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class UserMemory:
    """Local non-secret memory for stable user preferences."""

    timezone: str = "Asia/Yerevan"
    # This vendor relationship is Persian interpretation only -- a known
    # fact about the interpreter's employment, not per-email data. Every
    # schedule event and coverage slot gets this assigned unconditionally
    # (see scheduler_flow.py's validate_schedule/handle_coverage_request),
    # not extracted or validated per email.
    default_language: str = "Persian"
    scheduler_email: str = "scheduler@example.com"
    calendar_name: str = "Work"
    # Read from VENDOR_ID in .env (gitignored, never committed) rather than
    # hardcoded, so this real identifier never sits in source control -- the
    # public repo's own default stays the generic placeholder. A
    # default_factory (not a plain class-level default) is required here:
    # this module gets imported before main.py's load_dotenv() call runs, so
    # a plain `= os.getenv(...)` default would freeze in at import time and
    # never see the real .env value.
    vendor_id: str = field(default_factory=lambda: os.getenv("VENDOR_ID", "000000"))

    def snapshot(self) -> dict[str, object]:
        return {
            "timezone": self.timezone,
            "default_language": self.default_language,
            "scheduler_email": self.scheduler_email,
            "calendar_name": self.calendar_name,
            "vendor_id": self.vendor_id,
        }

