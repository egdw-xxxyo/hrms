# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

"""Server side of the Attendance Sheet page.

The page is open to everyone, and the reporting line is the only thing that decides
what a user may do with it: no role widens it and none is required. Every method that
writes therefore checks the employee against `get_editable_employees` before it touches
a document, because the write itself runs with permissions ignored.
"""

import frappe
from frappe import _
from frappe.utils import cstr, flt, formatdate, getdate

from hrms.hr.doctype.attendance_sheet_approval.attendance_sheet_approval import (
	get_approval_for,
	validate_not_approved,
)
from hrms.utils import get_date_range

MAX_PERIOD_DAYS = 90

ATTENDANCE_STATUSES = ("Present", "Work From Home", "Half Day", "Absent", "Sick Leave")


def get_session_employee() -> str | None:
	return frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, "name")


def get_editable_employees(company: str | None = None) -> dict[str, dict]:
	"""Returns the employees the current user may fill the sheet for.

	The direct reports of the employee the session user is linked to, one level of
	`reports_to`, plus whoever HR listed on that employee's card as an addition —
	the way somebody without a manager of their own still lands in a sheet. Roles
	grant nothing here on purpose: an HR manager or an administrator without reports
	gets an empty sheet, exactly like anybody else.

	The additions come first, ahead of the reports, and the order carries into the page.
	"""
	own = get_session_employee()
	if not own:
		return {}

	added = get_extra_employees(own)
	extra = fetch_employees({"name": ["in", added]}, company) if added else {}
	reports = fetch_employees({"reports_to": own}, company)

	return extra | {name: entry for name, entry in reports.items() if name not in extra}


def get_extra_employees(manager: str) -> list[str]:
	"""The employees HR added to this manager's sheet by hand, an empty list if none.

	The manager themselves is dropped: the field is validated against it on the Employee
	form, and nobody marks their own attendance even if a stale row survives somewhere.
	"""
	rows = frappe.get_all(
		"Attendance Sheet Extra Employee",
		filters={
			"parenttype": "Employee",
			"parentfield": "attendance_sheet_extra_employees",
			"parent": manager,
		},
		pluck="employee",
		ignore_permissions=True,
	)

	return [employee for employee in rows if employee != manager]


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def extra_employee_query(
	doctype: str,
	txt: str,
	searchfield: str,
	start: int,
	page_len: int,
	filters: dict | None,
) -> list:
	"""Link query for the additions field on the Employee form.

	Offers everybody the manager does not already have: their own direct reports are in
	the sheet by hierarchy, and they themselves never belong in it. User permissions are
	bypassed on purpose — the whole point of the field is to reach somebody outside the
	manager's own subtree, typically the person above them — so the gate is the right to
	edit an Employee at all, which only HR has.
	"""
	if not frappe.has_permission("Employee", "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	manager = (filters or {}).get("manager")
	excluded = []
	if manager:
		excluded = [manager, *fetch_employees({"reports_to": manager})]

	scope = {"status": "Active"}
	if excluded:
		scope["name"] = ["not in", excluded]

	return frappe.get_all(
		"Employee",
		filters=scope,
		or_filters={"name": ["like", f"%{txt}%"], "employee_name": ["like", f"%{txt}%"]},
		fields=["name", "employee_name"],
		start=start,
		page_length=page_len,
		order_by="employee_name",
		as_list=True,
		ignore_permissions=True,
	)


def fetch_employees(filters: dict, company: str | None = None) -> dict[str, dict]:
	scope = {"status": "Active", **filters}
	if company:
		scope["company"] = company

	employees = frappe.get_all(
		"Employee",
		filters=scope,
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
	return build_sheet(company, *validate_period(from_date, to_date))


def build_sheet(company: str, from_date, to_date) -> dict:
	"""Returns one row per employee with a cell for every day of the period.

	Kept apart from the whitelisted entry point: its dates are already parsed, and
	the type validation on a whitelisted call would refuse them.
	"""
	employees = get_editable_employees(company)
	dates = [getdate(d) for d in get_date_range(from_date, to_date)]

	if not employees:
		return {"employees": [], "dates": [cstr(d) for d in dates], "can_approve": False, "approval": None}

	attendance = get_attendance_map(list(employees), from_date, to_date)
	leaves = get_leave_map(list(employees), from_date, to_date)
	holidays = get_holiday_map(employees, company, from_date, to_date)
	locks = get_lock_map(list(employees), from_date, to_date)
	leave_abbrs = get_leave_abbreviations()

	rows = [
		{
			"employee": employee,
			"employee_name": details.employee_name,
			"days": {
				cstr(d): get_cell(
					employee,
					d,
					attendance,
					leaves,
					holidays.get(details.holiday_list) or {},
					locks,
					leave_abbrs,
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
		"approval": get_approval(manager, company, from_date, to_date),
	}


def get_cell(
	employee: str,
	day,
	attendance: dict,
	leaves: dict,
	holidays: dict,
	locks: dict,
	leave_abbrs: dict,
) -> dict:
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
		"leave_abbr": (leave_abbrs.get(leave.leave_type) if leave else None) or "",
		"overtime_hours": flt(entry.overtime_hours) if entry else 0.0,
		"shortfall_hours": flt(entry.shortfall_hours) if entry else 0.0,
		"shift": (entry.shift if entry else None) or "",
		"locked": day in locks.get(employee, set()),
	}


def get_leave_abbreviations() -> dict[str, str]:
	"""The mark each leave type leaves on a day, keyed by type.

	Set on the leave type itself, so a new kind of leave gets its own letters in the
	sheet without a code change. A type without one falls back to leave in general.
	"""
	types = frappe.get_all(
		"Leave Type",
		filters={"attendance_sheet_abbr": ("is", "set")},
		fields=["name", "attendance_sheet_abbr"],
		order_by="name",
		ignore_permissions=True,
	)
	return {entry.name: entry.attendance_sheet_abbr for entry in types}


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


def get_approval(manager: str | None, company: str, from_date, to_date) -> dict | None:
	"""The sheet this manager has already handed over for this period.

	Keyed by company as well: a manager whose reports sit in more than one of them
	approves each separately, and the state of one says nothing about the other.
	"""
	if not manager:
		return None

	approval = frappe.db.get_value(
		"Attendance Sheet Approval",
		{
			"manager": manager,
			"company": company,
			"from_date": from_date,
			"to_date": to_date,
			"docstatus": 1,
		},
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
def get_leave(name: str) -> dict:
	"""The application behind a day, for the dialog that edits it.

	The manager has no read permission on their report's documents, so the page reads
	them here, past the same check that guards every write.
	"""
	leave = frappe.db.get_value(
		"Leave Application",
		name,
		[
			"name",
			"employee",
			"leave_type",
			"from_date",
			"to_date",
			"half_day",
			"half_day_date",
			"description",
		],
		as_dict=True,
	)

	assert_can_edit([leave.employee])

	return leave


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
def clear_range(employees: str | list, from_date: str, to_date: str, company: str | None = None) -> dict:
	"""Removes what the sheet holds over a period, for one employee or for a whole column.

	A leave that reaches beyond the period is kept: the selection says nothing about the
	days outside it, and dropping the application would take those days along with it.
	"""
	employees = frappe.parse_json(employees) if isinstance(employees, str) else employees
	from_date, to_date = validate_period(from_date, to_date)
	assert_can_edit(employees, company)

	deleted = 0
	skipped = []
	kept_leaves = set()

	for leave in frappe.get_all(
		"Leave Application",
		filters={
			"docstatus": ("<", 2),
			"employee": ("in", employees),
			"from_date": ("<=", to_date),
			"to_date": (">=", from_date),
		},
		fields=["name", "employee", "from_date", "to_date"],
		ignore_permissions=True,
	):
		if getdate(leave.from_date) < from_date or getdate(leave.to_date) > to_date:
			kept_leaves.add(leave.name)
			skipped.append(
				{
					"employee": leave.employee,
					"date": f"{formatdate(leave.from_date)} — {formatdate(leave.to_date)}",
					"reason": _("The leave reaches outside the selected period"),
				}
			)
			continue

		result = remove_document(cancel_leave, leave.name, leave.employee, leave.from_date)
		deleted, skipped = count_removal(result, deleted, skipped)

	for record in frappe.get_all(
		"Attendance",
		filters={
			"docstatus": ("<", 2),
			"employee": ("in", employees),
			"attendance_date": ("between", [from_date, to_date]),
		},
		fields=["name", "employee", "attendance_date", "leave_application"],
		ignore_permissions=True,
	):
		# the day of a leave that stays would otherwise lose the record behind it
		if record.leave_application in kept_leaves:
			continue

		result = remove_document(drop_attendance, record.name, record.employee, record.attendance_date)
		deleted, skipped = count_removal(result, deleted, skipped)

	return {"deleted": deleted, "skipped": skipped}


def remove_document(remove, name: str, employee: str, day) -> dict:
	"""Drops one document, so that a refusal costs its own record and not the batch."""
	save_point = "clear_attendance_range"
	frappe.db.savepoint(save_point)

	try:
		validate_not_approved(employee, getdate(day))
		remove(name)

		frappe.db.release_savepoint(save_point)
		return {}
	except Exception as e:
		frappe.db.rollback(save_point=save_point)
		skipped = {
			"employee": employee,
			"date": formatdate(day),
			"reason": get_failure_reason(e),
		}
		frappe.clear_messages()
		return {"skipped": skipped}


def count_removal(result: dict, deleted: int, skipped: list) -> tuple:
	if result.get("skipped"):
		return deleted, [*skipped, result["skipped"]]

	return deleted + 1, skipped


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

	sheet = build_sheet(company, from_date, to_date)

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
	"""The numbers of the summarized view, for one employee."""
	totals = {
		"total_present": 0.0,
		"total_leave": 0.0,
		"total_sick": 0.0,
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
		elif status == "Sick Leave":
			totals["total_sick"] += 1
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

	if doc.manager != get_session_employee():
		frappe.throw(_("Only {0} can reopen this period").format(doc.manager), frappe.PermissionError)

	doc.flags.ignore_permissions = True
	doc.cancel()


@frappe.whitelist()
def get_approval_of(employee: str, date: str) -> str | None:
	return get_approval_for(employee, date)
