from __future__ import annotations

from datetime import date

# NSE equity segment trading holidays — 2025 & 2026
# Source: NSE India official holiday calendar
NSE_HOLIDAYS: set[date] = {
    # 2025
    date(2025, 1, 26),   # Republic Day
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr (Ramadan Eid)
    date(2025, 4, 10),   # Shri Ram Navami
    date(2025, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Mahatma Gandhi Jayanti
    date(2025, 10, 2),   # Dussehra
    date(2025, 10, 20),  # Diwali Laxmi Pujan (Muhurat trading day — kept as holiday)
    date(2025, 10, 21),  # Diwali Balipratipada
    date(2025, 11, 5),   # Prakash Gurpurab Sri Guru Nanak Dev Ji
    date(2025, 12, 25),  # Christmas

    # 2026
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 20),   # Holi
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 6),    # Gudi Padwa / Ugadi
    date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day / Labour Day
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 11, 9),   # Diwali Laxmi Pujan
    date(2026, 11, 10),  # Diwali Balipratipada
    date(2026, 12, 25),  # Christmas
}


def is_holiday(d: date | None = None) -> bool:
    if d is None:
        d = date.today()
    return d in NSE_HOLIDAYS
