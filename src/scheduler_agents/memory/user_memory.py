from __future__ import annotations

from dataclasses import dataclass


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
    vendor_id: str = "000000"

    def snapshot(self) -> dict[str, object]:
        return {
            "timezone": self.timezone,
            "default_language": self.default_language,
            "scheduler_email": self.scheduler_email,
            "calendar_name": self.calendar_name,
            "vendor_id": self.vendor_id,
        }

