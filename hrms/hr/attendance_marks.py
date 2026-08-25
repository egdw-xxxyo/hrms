# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

"""What a day of attendance looks like, for everything that draws one.

The Attendance Sheet page and the Monthly Attendance Sheet report show the same days in
the same letters and the same colours, and differ only in how they put them on screen.
The marks live here so that the two cannot drift apart.
"""

import frappe
from frappe import _

ABBR_CONTEXT = "Attendance Sheet Abbreviation"

# the mark each status leaves on a day and the colour it carries, in reading order
STATUS_META = {
	"Present": ("P", "green"),
	"Work From Home": ("WFH", "green"),
	"Absent": ("A", "red"),
	"Sick Leave": ("SL", "#8B5CF6"),
	"Half Day": ("HD", "orange"),
	"On Leave": ("L", "#3187D8"),
	"Holiday": ("H", "#878787"),
	"Weekly Off": ("WO", "#878787"),
}

MUTED_COLOR = "#878787"

DAY_CONTEXT = "Day of Week Abbreviation"

DAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def get_abbr(status: str) -> str:
	"""The mark a status leaves on a day, in the language of the site."""
	meta = STATUS_META.get(status)

	return _(meta[0], context=ABBR_CONTEXT) if meta else ""


def get_color(status: str) -> str:
	meta = STATUS_META.get(status)

	return meta[1] if meta else MUTED_COLOR


def get_leave_abbreviations() -> dict[str, str]:
	"""The mark each leave type leaves on a day, keyed by type.

	Set on the leave type itself, so a new kind of leave gets its own letters without a
	code change. A type without one falls back to leave in general.
	"""
	types = frappe.get_all(
		"Leave Type",
		filters={"attendance_sheet_abbr": ("is", "set")},
		fields=["name", "attendance_sheet_abbr"],
		order_by="name",
		ignore_permissions=True,
	)

	return {entry.name: entry.attendance_sheet_abbr for entry in types}


def get_unpaid_leave_types() -> set[str]:
	"""The leave types nobody is paid for, the ones taken at the employee's own expense.

	A day of one of them is an absence as far as payroll is concerned, so the summarized
	view counts it in the absence column rather than among the leaves.
	"""
	return set(frappe.get_all("Leave Type", filters={"is_lwp": 1}, pluck="name", ignore_permissions=True))


def get_day_label(day) -> str:
	"""How a day of the period is headed: its number and the weekday after it."""
	return f"{day.day} {_(DAY_ABBR[day.weekday()], context=DAY_CONTEXT)}"
