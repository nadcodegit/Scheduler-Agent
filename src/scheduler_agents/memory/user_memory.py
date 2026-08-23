from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UserMemory:
    """Local non-secret memory for stable user preferences."""

    timezone: str = "Asia/Yerevan"
    languages: list[str] = field(default_factory=lambda: ["English", "Turkish", "Persian"])
    # This vendor relationship is Persian-only, and real schedule/roster/
    # coverage emails never state a language at all -- it's implicit
    # context on their side. Extraction paths (LLM crew, regex, roster
    # vision) backfill a missing language from this rather than leaving it
    # unset, so the deterministic guardrail validates real data instead of
    # false-positive-blocking on a field the source was never going to have.
    default_language: str = "Persian"
    scheduler_email: str = "scheduler@example.com"
    calendar_name: str = "Work"
    vendor_id: str = "000000"

    def snapshot(self) -> dict[str, object]:
        return {
            "timezone": self.timezone,
            "languages": list(self.languages),
            "default_language": self.default_language,
            "scheduler_email": self.scheduler_email,
            "calendar_name": self.calendar_name,
            "vendor_id": self.vendor_id,
        }

