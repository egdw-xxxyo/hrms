# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

"""Server side of the Attendance Sheet page.

The page is open to everyone: what a user may do is decided here, by who reports to
them, and not by the roles on the page or on Attendance. Every method that writes
therefore checks the employee against `get_editable_employees` before it touches a
document, because the write itself runs with permissions ignored.
"""

import frappe
from frappe import _
from frappe.utils import cstr, flt, formatdate, getdate

from hrms.hr.doctype.attendance_sheet_approval.attendance_sheet_approval import (
	get_approval_for,
	validate_not_approved,
)
from hrms.utils import get_date_range

HR_ROLES = ("HR User", "HR Manager", "System Manager")

MAX_PERIOD_DAYS = 90

ATTENDANCE_STATUSES = ("Present", "Work From Home", "Half Day", "Absent")


def has_hr_access() -> bool:
	return bool(set(HR_ROLES) & set(frappe.get_roles()))


def get_session_employee() -> str | None:
	return frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, "name")


def get_editable_employees(company: str | None = None) -> dict[str, dict]:
	"""Returns the employees the current user may fill the sheet for.

	HR sees the whole company. Everyone else sees their direct reports only: one level
	of `reports_to`, without themselves, so nobody fills their own timesheet.
	"""
	filters = {"status": "Active"}
	if company:
		filters["company"] = company

	if not has_hr_access():
		manager = get_session_employee()
		if not manager:
			return {}
		filters["reports_to"] = manager

	employees = frappe.get_all(
		"Employee",
		filters=filters,
		fields=["name", "employee_name", "company", "holiday_list", "date_of_joining"],
		order_by="employee_name",
		ignore_permissions=True,
	)

	return {entry.name: entry for entry in employees}


def assert_can_edit(employees: list[str], company: str | None = None) -> None:
	editable = get_editable_employees(company)
	forbidden = [employee for employee in employees if employee not in editable]

	if forbidden:
		frappe.throw(
			_("You are not permitted to fill the timesheet for {0}").format(", ".join(forbidden)),
			frappe.PermissionError,
		)


def validate_period(from_date: str, to_date: str) -> tuple:
	from_date, to_date = getdate(from_date), getdate(to_date)

	if from_date > to_date:
		frappe.throw(_("Start date cannot be greater than end date."))
	if (to_date - from_date).days > MAX_PERIOD_DAYS:
		frappe.throw(_("Please set a date range less than {0} days.").format(MAX_PERIOD_DAYS))

	return from_date, to_date


@frappe.whitelist()
def get_companies() -> list[str]:
	"""Companies of the employees the user can see.

	The page cannot offer a Company link filter: stock Company grants no permission to
	any HR role, so the link validation the desk runs on it answers 403 for everyone
	but a System Manager.
	"""
	companies = sorted({entry.company for entry in get_editable_employees().values() if entry.company})

	return companies or [frappe.defaults.get_user_default("Company")]


@frappe.whitelist()
def get_sheet(company: str, from_date: str, to_date: str) -> dict:
	"""Returns one row per employee with a cell for every day of the period."""
	from_date, to_date = validate_period(from_date, to_date)
	employees = get_editable_employees(company)
	dates = [getdate(d) for d in get_date_range(from_date, to_date)]

	if not employees:
		return {"employees": [], "dates": [cstr(d) for d in dates], "can_approve": False, "approval": None}

	attendance = get_attendance_map(list(employees), from_date, to_date)
	leaves = get_leave_map(list(employees), from_date, to_date)
	holidays = get_holiday_map(employees, company, from_date, to_date)
	locks = get_lock_map(list(employees), from_date, to_date)

	rows = [
		{
			"employee": employee,
			"employee_name": details.employee_name,
			"days": {
				cstr(d): get_cell(
					employee, d, attendance, leaves, holidays.get(details.holiday_list) or {}, locks
				)
				for d in dates
			},
		}
		for employee, details in employees.items()
	]

	manager = get_session_employee()

	return {
		"employees": rows,
		"dates": [cstr(d) for d in dates],
		"can_approve": bool(manager),
		"approval": get_approval(manager, from_date, to_date),
	}


def get_cell(employee: str, day, attendance: dict, leaves: dict, holidays: dict, locks: dict) -> dict:
	entry = attendance.get(employee, {}).get(day)
	leave = leaves.get(employee, {}).get(day)

	status = entry.status if entry else None
	if not status and leave:
		status = "On Leave"
	if not status:
		status = holidays.get(day)

	return {
		"status": status or "",
		"half_day_status": (entry.half_day_status if entry else None) or "",
		"attendance": entry.name if entry else None,
		"leave_application": (entry.leave_application if entry else None) or (leave.name if leave else None),
		"overtime_hours": flt(entry.overtime_hours) if entry else 0.0,
		"shortfall_hours": flt(entry.shortfall_hours) if entry else 0.0,
		"shift": (entry.shift if entry else None) or "",
		"locked": day in locks.get(employee, set()),
	}


def get_attendance_map(employees: list[str], from_date, to_date) -> dict:
	"""Approved attendance per employee and day.

	A day may hold more than one record when shifts overlap; the sheet is one row per
	employee, so the last one filed wins the cell and the rest stay reachable through
	the Attendance list.
	"""
	records = frappe.get_all(
		"Attendance",
		filters={
			"docstatus": 1,
			"employee": ("in", employees),
			"attendance_date": ("between", [from_date, to_date]),
		},
		fields=[
			"name",
			"employee",
			"attendance_date",
			"status",
			"half_day_status",
			"overtime_hours",
			"shortfall_hours",
			"leave_application",
			"shift",
		],
		order_by="attendance_date, creation",
		ignore_permissions=True,
	)

	attendance = {}
	for entry in records:
		attendance.setdefault(entry.employee, {})[getdate(entry.attendance_date)] = entry

	return attendance


def get_leave_map(employees: list[str], from_date, to_date) -> dict:
	"""Approved leave per employee and day.

	Attendance is the source of truth for a day that has it, but a leave whose
	attendance was never created would otherwise leave the day looking empty.
	"""
	records = frappe.get_all(
		"Leave Application",
		filters={
			"docstatus": 1,
			"status": "Approved",
			"employee": ("in", employees),
			"from_date": ("<=", to_date),
			"to_date": (">=", from_date),
		},
		fields=["name", "employee", "leave_type", "from_date", "to_date", "half_day", "half_day_date"],
		ignore_permissions=True,
	)

	leaves = {}
	for leave in records:
		start = max(getdate(leave.from_date), from_date)
		end = min(getdate(leave.to_date), to_date)

		for d in get_date_range(start, end):
			leaves.setdefault(leave.employee, {})[getdate(d)] = leave

	return leaves


def get_holiday_map(employees: dict, company: str, from_date, to_date) -> dict:
	default_list = frappe.get_cached_value("Company", company, "default_holiday_list")
	lists = {details.holiday_list or default_list for details in employees.values()}

	holiday_map = {}
	for holiday_list in lists:
		if not holiday_list:
			continue

		holidays = frappe.get_all(
			"Holiday",
			filters={
				"parent": holiday_list,
				"holiday_date": ("between", [from_date, to_date]),
			},
			fields=["holiday_date", "weekly_off"],
			ignore_permissions=True,
		)
		holiday_map[holiday_list] = {
			getdate(holiday.holiday_date): "Weekly Off" if holiday.weekly_off else "Holiday"
			for holiday in holidays
		}

	for details in employees.values():
		if not details.holiday_list:
			details.holiday_list = default_list

	return holiday_map


def get_lock_map(employees: list[str], from_date, to_date) -> dict:
	"""Days already handed to the accounting, per employee."""
	Approval = frappe.qb.DocType("Attendance Sheet Approval")
	Row = frappe.qb.DocType("Attendance Sheet Approval Employee")

	approvals = (
		frappe.qb.from_(Approval)
		.join(Row)
		.on(Row.parent == Approval.name)
		.select(Row.employee, Approval.from_date, Approval.to_date)
		.where(
			(Approval.docstatus == 1)
			& (Approval.from_date <= to_date)
			& (Approval.to_date >= from_date)
			& (Row.employee.isin(employees))
		)
	).run(as_dict=True)

	locks = {}
	for approval in approvals:
		start = max(getdate(approval.from_date), from_date)
		end = min(getdate(approval.to_date), to_date)
		locks.setdefault(approval.employee, set()).update(getdate(d) for d in get_date_range(start, end))

	return locks


def get_approval(manager: str | None, from_date, to_date) -> dict | None:
	if not manager:
		return None

	approval = frappe.db.get_value(
		"Attendance Sheet Approval",
		{"manager": manager, "from_date": from_date, "to_date": to_date, "docstatus": 1},
		["name", "creation"],
		as_dict=True,
	)

	return approval


@frappe.whitelist()
def save_attendance(
	employees: str | list,
	from_date: str,
	to_date: str,
	status: str,
	half_day_status: str | None = None,
	overtime_hours: float = 0,
	shortfall_hours: float = 0,
	shift: str | None = None,
	company: str | None = None,
) -> dict:
	"""Marks every day of the period for every employee given, as an approved record.

	Both gestures of the page end up here: a range dragged across one row is many days
	for one employee, a day picked from the column header is one day for many. A day
	the doctype refuses is reported back instead of failing the whole batch.
	"""
	employees = frappe.parse_json(employees) if isinstance(employees, str) else employees
	from_date, to_date = validate_period(from_date, to_date)
	assert_can_edit(employees, company)
	validate_status(status)
	validate_hours(overtime_hours, shortfall_hours)

	created, skipped = [], []

	for employee in employees:
		for day in get_date_range(from_date, to_date):
			day = getdate(day)
			outcome = mark_day(
				employee,
				day,
				{
					"status": status,
					"half_day_status": half_day_status if status == "Half Day" else None,
					"overtime_hours": flt(overtime_hours),
					"shortfall_hours": flt(shortfall_hours),
					"shift": shift,
				},
			)

			if outcome.get("name"):
				created.append(outcome["name"])
			else:
				skipped.append(outcome["skipped"])

	return {"created": created, "skipped": skipped}


def mark_day(employee: str, day, values: dict) -> dict:
	"""Replaces whatever the employee has on that day with one approved record.

	Attendance keeps `status` out of `allow_on_submit`, so a marked day cannot be
	edited in place: the record that holds it is dropped and a new one takes its place.
	Both happen inside one savepoint, so a refused day leaves the old record alone.
	"""
	save_point = "mark_attendance_day"
	frappe.db.savepoint(save_point)

	try:
		validate_not_approved(employee, day)
		remove_attendance_on(employee, day)

		doc = frappe.new_doc("Attendance")
		doc.update(
			{
				"employee": employee,
				"company": frappe.db.get_value("Employee", employee, "company"),
				"attendance_date": day,
				**values,
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()

		frappe.db.release_savepoint(save_point)
		return {"name": doc.name}
	except Exception as e:
		frappe.db.rollback(save_point=save_point)
		skipped = {
			"employee": employee,
			"date": formatdate(day),
			"reason": get_failure_reason(e),
		}
		frappe.clear_messages()
		return {"skipped": skipped}


def remove_attendance_on(employee: str, day) -> None:
	existing = frappe.get_all(
		"Attendance",
		filters={"employee": employee, "attendance_date": day, "docstatus": ("<", 2)},
		pluck="name",
		ignore_permissions=True,
	)

	for name in existing:
		drop_attendance(name)


def drop_attendance(name: str) -> None:
	doc = frappe.get_doc("Attendance", name)
	if doc.docstatus == 1:
		doc.flags.ignore_permissions = True
		doc.cancel()

	frappe.delete_doc("Attendance", name, force=True, delete_permanently=True, ignore_permissions=True)


def validate_status(status: str) -> None:
	if status not in ATTENDANCE_STATUSES:
		frappe.throw(_("{0} is not a status the timesheet can set").format(status))


def validate_hours(overtime_hours: float, shortfall_hours: float) -> None:
	if flt(overtime_hours) and flt(shortfall_hours):
		frappe.throw(_("A day is either worked over or under, not both"))


def get_failure_reason(exception: Exception) -> str:
	"""The message the doctype refused the day with, without its markup."""
	messages = [frappe.utils.strip_html(cstr(m.get("message"))).strip() for m in frappe.get_message_log()]
	messages = [message for message in messages if message]

	return messages[-1] if messages else cstr(exception)


@frappe.whitelist()
def delete_attendance(name: str) -> None:
	employee, attendance_date = frappe.db.get_value("Attendance", name, ["employee", "attendance_date"])
	assert_can_edit([employee])
	validate_not_approved(employee, attendance_date)

	drop_attendance(name)


@frappe.whitelist()
def get_leave_details(employee: str, date: str) -> dict:
	from hrms.hr.doctype.leave_application.leave_application import get_leave_details as leave_details

	assert_can_edit([employee])

	return leave_details(employee, date)


@frappe.whitelist()
def save_leave(
	employee: str,
	leave_type: str,
	from_date: str,
	to_date: str,
	half_day: int = 0,
	half_day_date: str | None = None,
	description: str | None = None,
	name: str | None = None,
) -> dict:
	"""Files a leave that is already approved.

	The timesheet has no pending state: what a manager enters for their report is the
	decision itself, so the application is submitted as Approved right away and the
	doctype writes the attendance behind it.
	"""
	from_date, to_date = validate_period(from_date, to_date)
	assert_can_edit([employee])

	for day in get_date_range(from_date, to_date):
		validate_not_approved(employee, day)

	if name:
		cancel_leave(name)

	doc = frappe.new_doc("Leave Application")
	doc.update(
		{
			"employee": employee,
			"leave_type": leave_type,
			"from_date": from_date,
			"to_date": to_date,
			"half_day": int(half_day or 0),
			"half_day_date": half_day_date if int(half_day or 0) else None,
			"description": description,
			"status": "Approved",
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()

	return {"name": doc.name}


@frappe.whitelist()
def delete_leave(name: str) -> None:
	employee = frappe.db.get_value("Leave Application", name, "employee")
	assert_can_edit([employee])

	cancel_leave(name)


def cancel_leave(name: str) -> None:
	doc = frappe.get_doc("Leave Application", name)

	for day in get_date_range(getdate(doc.from_date), getdate(doc.to_date)):
		validate_not_approved(doc.employee, day)

	if doc.docstatus == 1:
		doc.flags.ignore_permissions = True
		doc.cancel()

	frappe.delete_doc("Leave Application", name, force=True, delete_permanently=True, ignore_permissions=True)


@frappe.whitelist()
def approve_sheet(company: str, from_date: str, to_date: str) -> dict:
	"""Freezes the period and stores the totals that go to the accounting."""
	from_date, to_date = validate_period(from_date, to_date)
	manager = get_session_employee()

	if not manager:
		frappe.throw(_("Only an employee can approve a timesheet"), frappe.PermissionError)

	employees = get_editable_employees(company)
	if not employees:
		frappe.throw(_("There is nothing to approve"))

	sheet = get_sheet(company, from_date, to_date)

	doc = frappe.new_doc("Attendance Sheet Approval")
	doc.update(
		{
			"manager": manager,
			"company": company,
			"from_date": from_date,
			"to_date": to_date,
			"employees": [
				{"employee": row["employee"], **get_totals(row["days"].values())}
				for row in sheet["employees"]
			],
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()

	return {"name": doc.name}


def get_totals(cells) -> dict:
	"""The five numbers of the summarized view, for one employee."""
	totals = {
		"total_present": 0.0,
		"total_leave": 0.0,
		"total_absent": 0.0,
		"overtime_hours": 0.0,
		"shortfall_hours": 0.0,
	}

	for cell in cells:
		status = cell["status"]

		if status in ("Present", "Work From Home"):
			totals["total_present"] += 1
		elif status == "On Leave":
			totals["total_leave"] += 1
		elif status == "Absent":
			totals["total_absent"] += 1
		elif status == "Half Day":
			totals["total_present"] += 0.5
			other_half = "total_present" if cell["half_day_status"] == "Present" else "total_absent"
			totals[other_half] += 0.5

		totals["overtime_hours"] += flt(cell["overtime_hours"])
		totals["shortfall_hours"] += flt(cell["shortfall_hours"])

	return totals


@frappe.whitelist()
def cancel_approval(name: str) -> None:
	doc = frappe.get_doc("Attendance Sheet Approval", name)

	if doc.manager != get_session_employee() and not has_hr_access():
		frappe.throw(_("Only {0} can reopen this period").format(doc.manager), frappe.PermissionError)

	doc.flags.ignore_permissions = True
	doc.cancel()


@frappe.whitelist()
def get_approval_of(employee: str, date: str) -> str | None:
	return get_approval_for(employee, date)
