import frappe
from frappe.utils import flt

DEFAULT_DAY_HOURS = 8.0


def execute():
	"""Rewrites every half day into a status the timesheet still knows.

	A half day always meant the same thing: the employee worked half of it. The hours on
	the day say that better, so the day becomes a present one carrying the hours nobody
	worked as a shortfall. The old `half_day_status` only noted which half was missing,
	which changes nothing about how much was worked, so it is dropped.

	A day that hangs off a leave application is the exception: the doctype ties such a day
	to its leave and would set it back to `On Leave` on the next save, so it goes there now.
	"""
	if not frappe.db.has_column("Attendance", "half_day_status"):
		return

	rows = frappe.db.sql(
		"""
		select name, leave_application, overtime_hours, shortfall_hours
		from `tabAttendance`
		where status = 'Half Day' and docstatus < 2
		""",
		as_dict=True,
	)

	if not rows:
		return

	missing_half = day_hours() / 2

	for row in rows:
		frappe.db.set_value(
			"Attendance",
			row.name,
			resolve(row, missing_half),
			update_modified=False,
		)


def day_hours() -> float:
	return flt(frappe.db.get_single_value("HR Settings", "standard_working_hours")) or DEFAULT_DAY_HOURS


def resolve(row, missing_half: float) -> dict:
	if row.leave_application:
		return {"status": "On Leave", "half_day_status": None}

	overtime, shortfall = net_hours(row, missing_half)

	return {
		"status": "Present",
		"half_day_status": None,
		"overtime_hours": overtime,
		"shortfall_hours": shortfall,
	}


def net_hours(row, missing: float) -> tuple[float, float]:
	"""The day carries either overtime or a shortfall, never both, so they net out."""
	balance = flt(row.overtime_hours) - flt(row.shortfall_hours) - missing

	return (balance, 0.0) if balance >= 0 else (0.0, -balance)
