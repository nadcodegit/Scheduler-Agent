from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UserMemory:
    """Local non-secret memory for stable user preferences."""

    timezone: str = "Asia/Yerevan"
    languages: list[str] = field(default_factory=lambda: ["English", "Turkish", "Persian"])
    scheduler_email: str = "scheduler@example.com"
    calendar_name: str = "Work"

    def snapshot(self) -> dict[str, object]:
        return {
            "timezone": self.timezone,
            "languages": list(self.languages),
            "scheduler_email": self.scheduler_email,
            "calendar_name": self.calendar_name,
        }

