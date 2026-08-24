# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


from calendar import monthrange
from datetime import date
from itertools import groupby

from pypika import Field
from pypika.terms import Criterion

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Extract, Sum
from frappe.utils import cint, cstr, flt, formatdate, getdate
from frappe.utils.nestedset import get_descendants_of

from hrms.hr.attendance_marks import (
	STATUS_META,
	get_abbr,
	get_color,
	get_day_label,
	get_leave_abbreviations,
)
from hrms.hr.doctype.attendance_sheet_approval.attendance_sheet_approval import get_approved_periods
from hrms.utils import date_diff, get_date_range

Filters = frappe._dict

# the report splits a half day by what the other half was; the statuses are its own
half_day_map = {
	"Half Day/Other Half Present": "Present",
	"Half Day/Other Half Absent": "Absent",
}


def execute(filters: Filters | None = None) -> tuple:
	filters = frappe._dict(filters or {})

	if not filters.filter_based_on:
		frappe.throw(_("Please select Filter Based On"))

	if filters.filter_based_on == "Month" and not (filters.month and filters.year):
		frappe.throw(_("Please select month and year."))

	if filters.filter_based_on == "Date Range":
		if not (filters.start_date and filters.end_date):
			frappe.throw(_("Please set the date range."))
		if getdate(filters.start_date) > getdate(filters.end_date):
			frappe.throw(_("Start date cannot be greater than end date."))
		if date_diff(filters.end_date, filters.start_date) > 90:
			frappe.throw(_("Please set a date range less than 90 days."))

	if not filters.company:
		frappe.throw(_("Please select company."))

	if filters.company:
		filters.companies = [filters.company]
		if filters.include_company_descendants:
			filters.companies.extend(get_descendants_of("Company", filters.company))

	filters.approved_periods = get_approved_periods_in_period(filters)

	attendance_map = get_attendance_map(filters)
	if not attendance_map:
		message = (
			_("No attendance records found.")
			if filters.approved_periods
			else _("No approved attendance sheet found for this period.")
		)
		frappe.msgprint(message, alert=True, indicator="orange")
		return [], [], None, None

	columns = get_columns(filters)
	data = get_data(filters, attendance_map)

	if not data:
		frappe.msgprint(_("No attendance records found for this criteria."), alert=True, indicator="orange")
		return columns, [], None, None

	message = get_message() if not filters.summarized_view else ""
	# the chart is off unless it is asked for: the legend below the table is what the sheet
	# is read with, and the chart pushes it a screen down
	chart = get_chart_data(attendance_map, filters) if filters.show_chart else None

	return columns, data, message, chart


def get_approved_periods_in_period(filters: Filters) -> dict[str, list[tuple]]:
	"""The days of the period each employee has already been handed over to payroll."""
	dates_in_period = get_dates_in_period(filters)

	return get_approved_periods(dates_in_period[0], dates_in_period[-1], filters.employee)


def is_approved(filters: Filters, employee: str, day: date) -> bool:
	"""Whether a day of an employee sits inside a submitted sheet.

	The report shows nothing else: what it prints is what payroll has been handed, and a
	day still open in a manager's sheet can change tomorrow.
	"""
	return any(start <= day <= end for start, end in filters.approved_periods.get(employee, []))


def get_mark(status: str) -> str:
	"""The mark a status leaves on a day of the report.

	A half day carries the letters of both halves, its own over the other one's, so the
	split the report makes stays readable without a status of its own in the legend.
	"""
	other_half = half_day_map.get(status)

	return f"{get_abbr('Half Day')}/{get_abbr(other_half)}" if other_half else get_abbr(status)


def get_message() -> str:
	"""The legend under the table: every mark a day can carry, in reading order.

	The half day sits in the legend as the two marks that actually reach a cell, since the
	report never leaves it whole.
	"""
	statuses = [
		entry for status in STATUS_META for entry in (half_day_map if status == "Half Day" else [status])
	]

	entries = [
		f"""
			<span style='border-left: 2px solid {get_mark_color(status)}; padding-right: 12px; padding-left: 5px; margin-right: 3px;'>
				{_(status)} - {get_mark(status)}
			</span>
		"""
		for status in statuses
	]

	return "".join(entries)


def get_mark_color(status: str) -> str:
	"""A half day is coloured by the half day, whichever way the other half went."""
	return get_color("Half Day" if status in half_day_map else status)


def get_columns(filters: Filters) -> list[dict]:
	columns = []

	if filters.group_by:
		options_mapping = {
			"Branch": "Branch",
			"Grade": "Employee Grade",
			"Department": "Department",
			"Designation": "Designation",
		}
		options = options_mapping.get(filters.group_by)
		columns.append(
			{
				"label": _(filters.group_by),
				"fieldname": frappe.scrub(filters.group_by),
				"fieldtype": "Link",
				"options": options,
				"width": 120,
			}
		)

	columns.extend(
		[
			{
				"label": _("Employee"),
				"fieldname": "employee",
				"fieldtype": "Link",
				"options": "Employee",
				"width": 135,
			},
			{"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 120},
		]
	)

	if filters.summarized_view:
		columns.extend(get_columns_for_totals())
	else:
		columns.append({"label": _("Shift"), "fieldname": "shift", "fieldtype": "Data", "width": 120})
		columns.extend(get_columns_for_days(filters))

	return columns


def get_columns_for_totals() -> list[dict]:
	"""The totals of the summarized view, the same set the attendance sheet page shows.

	Absence is the unpaid kind and nothing else, which is what the note in the label says
	and what makes the column readable by payroll.
	"""
	labels = {
		"total_present": _("Present Days"),
		"total_leave": _("Leave Days"),
		"total_sick": _("Sick Days"),
		"total_absent": f"{_('Absent Days')}, {_('at their own expense')}",
		"overtime_hours": _("Overtime Hours"),
		"shortfall_hours": _("Shortfall Hours"),
	}

	return [
		{
			"label": label,
			"fieldname": fieldname,
			"fieldtype": "Float",
			# the note the sheet page puts under the label has to fit on the line here
			"width": 250 if fieldname == "total_absent" else 150,
		}
		for fieldname, label in labels.items()
	]


def get_columns_for_days(filters: Filters) -> list[dict]:
	days = []
	dates_in_period = get_dates_in_period(filters)
	for d in dates_in_period:
		d = getdate(d)
		days.append(
			{
				"label": get_day_label(d),
				"fieldtype": "Data",
				"fieldname": d.strftime("%d-%m-%Y"),
				"align": "center",
				"width": 55,
			}
		)

	return days


def get_dates_in_period(filters: Filters) -> list[str]:
	dates_in_period = []
	if filters.filter_based_on == "Month":
		total_days = get_total_days_in_month(filters)
		# forms the datelist from selected year and month from filters
		dates_in_period = [
			f"{cstr(filters.year)}-{cstr(filters.month)}-{cstr(day)}" for day in range(1, total_days + 1)
		]
	if filters.filter_based_on == "Date Range":
		dates_in_period = get_date_range(filters.start_date, filters.end_date)

	return dates_in_period


def get_total_days_in_month(filters: Filters) -> int:
	return monthrange(cint(filters.year), cint(filters.month))[1]


def get_date_condition(docfield: Field, filters: Filters) -> Criterion:
	if filters.filter_based_on == "Month":
		return (Extract("month", docfield) == filters.month) & (Extract("year", docfield) == filters.year)
	if filters.filter_based_on == "Date Range":
		return (docfield >= filters.start_date) & (docfield <= filters.end_date)


def get_data(filters: Filters, attendance_map: dict) -> list[dict]:
	employee_details, group_by_param_values = get_employee_related_details(filters)
	holiday_map = get_holiday_map(filters)
	notes = {} if filters.summarized_view else get_day_notes(filters)
	data = []

	if filters.group_by:
		group_by_column = frappe.scrub(filters.group_by)

		for value in group_by_param_values:
			if not value:
				continue

			records = get_rows(employee_details[value], filters, holiday_map, attendance_map, notes)

			if records:
				data.append({group_by_column: value})
				data.extend(records)

	else:
		data = get_rows(employee_details, filters, holiday_map, attendance_map, notes)

	return data


def get_attendance_map(filters: Filters) -> dict:
	"""Returns a dictionary of employee wise attendance map as per shifts for all the days of the month like
	{
	    'employee1': {
	            'Morning Shift': {1: 'Present', 2: 'Absent', ...}
	            'Evening Shift': {1: 'Absent', 2: 'Present', ...}
	    },
	    'employee2': {
	            'Afternoon Shift': {1: 'Present', 2: 'Absent', ...}
	            'Night Shift': {1: 'Absent', 2: 'Absent', ...}
	    },
	    'employee3': {
	            None: {1: 'On Leave'}
	    }
	}
	"""
	attendance_list = get_attendance_records(filters)
	attendance_map = {}
	leave_map = {}

	for d in attendance_list:
		if d.status == "On Leave":
			leave_map.setdefault(d.employee, {}).setdefault(d.shift, []).append(d.attendance_date)
			continue

		if d.shift is None:
			d.shift = ""

		attendance_map.setdefault(d.employee, {}).setdefault(d.shift, {})
		attendance_map[d.employee][d.shift][d.attendance_date] = d.status

	# leave is applicable for the entire day so all shifts should show the leave entry

	for employee, leave_days in leave_map.items():
		for assigned_shift, dates in leave_days.items():
			# no attendance records exist except leaves
			if employee not in attendance_map:
				attendance_map.setdefault(employee, {}).setdefault(assigned_shift, {})

			for d in dates:
				for shift in attendance_map[employee].keys():
					attendance_map[employee][shift][d] = "On Leave"

	return attendance_map


def get_day_notes(filters: Filters) -> dict[tuple, dict]:
	"""The line under the mark of a day, keyed by employee and date.

	Either the hours the day ran over or short of the shift, or the kind of leave it was —
	the same second line the attendance sheet page draws, and in the same order of
	precedence: hours first, because they are the exception, and the leave otherwise.
	"""
	Attendance = frappe.qb.DocType("Attendance")
	query = (
		frappe.qb.from_(Attendance)
		.select(
			Attendance.employee,
			Attendance.attendance_date,
			Attendance.overtime_hours,
			Attendance.shortfall_hours,
			Attendance.leave_type,
		)
		.where(
			(Attendance.docstatus == 1)
			& (Attendance.company.isin(filters.companies))
			& get_date_condition(Attendance.attendance_date, filters)
		)
	)

	if filters.employee:
		query = query.where(Attendance.employee == filters.employee)

	leave_abbrs = get_leave_abbreviations()
	notes = {}

	for entry in query.run(as_dict=True):
		if not is_approved(filters, entry.employee, getdate(entry.attendance_date)):
			continue

		note = get_note(entry, leave_abbrs)
		if note:
			notes[(entry.employee, entry.attendance_date)] = note

	return notes


def get_note(entry: dict, leave_abbrs: dict) -> dict | None:
	"""The second line of a day: its text and which of the sheet's three kinds it is."""
	if entry.overtime_hours:
		return {"text": f"+{flt(entry.overtime_hours):g}", "kind": "over"}

	if entry.shortfall_hours:
		return {"text": f"-{flt(entry.shortfall_hours):g}", "kind": "under"}

	abbr = leave_abbrs.get(entry.leave_type)

	return {"text": abbr, "kind": "leave"} if abbr else None


def get_attendance_records(filters: Filters) -> list[dict]:
	Attendance = frappe.qb.DocType("Attendance")
	attendance_date_condition = get_date_condition(Attendance.attendance_date, filters)
	status = (
		frappe.qb.terms.Case()
		.when(
			((Attendance.status == "Half Day") & (Attendance.half_day_status == "Present")),
			"Half Day/Other Half Present",
		)
		.when(
			((Attendance.status == "Half Day") & (Attendance.half_day_status == "Absent")),
			"Half Day/Other Half Absent",
		)
		.else_(Attendance.status)
	)
	query = (
		frappe.qb.from_(Attendance)
		.select(
			Attendance.employee,
			Attendance.attendance_date,
			(status).as_("status"),
			Attendance.shift,
		)
		.where(
			(Attendance.docstatus == 1)
			& (Attendance.company.isin(filters.companies))
			& (attendance_date_condition)
		)
	)

	if filters.employee:
		query = query.where(Attendance.employee == filters.employee)
	query = query.orderby(Attendance.employee, Attendance.attendance_date)

	# a day is part of the report only once the sheet holding it has been handed over
	return [
		entry
		for entry in query.run(as_dict=1)
		if is_approved(filters, entry.employee, getdate(entry.attendance_date))
	]


def get_employee_related_details(filters: Filters) -> tuple[dict, list]:
	"""Returns
	1. nested dict for employee details
	2. list of values for the group by filter
	"""
	Employee = frappe.qb.DocType("Employee")

	joining_date_condition = get_date_condition(Employee.date_of_joining, filters)

	query = (
		frappe.qb.from_(Employee)
		.select(
			Employee.name,
			Employee.employee_name,
			Employee.designation,
			Employee.grade,
			Employee.department,
			Employee.branch,
			Employee.company,
			Employee.holiday_list,
			(Employee.date_of_joining).as_("joined_date"),
			Case()
			.when(
				joining_date_condition,
				1,
			)
			.else_(0)
			.as_("joined_in_current_period"),
		)
		.where(Employee.company.isin(filters.companies))
	)

	if filters.employee:
		query = query.where(Employee.name == filters.employee)

	group_by = filters.group_by
	if group_by:
		group_by = group_by.lower()
		query = query.orderby(group_by)

	employee_details = query.run(as_dict=True)

	group_by_param_values = []
	emp_map = {}

	if group_by:
		group_key = lambda d: "" if d[group_by] is None else d[group_by]  # noqa
		for parameter, employees in groupby(sorted(employee_details, key=group_key), key=group_key):
			group_by_param_values.append(parameter)
			emp_map.setdefault(parameter, frappe._dict())

			for emp in employees:
				emp_map[parameter][emp.name] = emp
	else:
		for emp in employee_details:
			emp_map[emp.name] = emp

	return emp_map, group_by_param_values


def get_holiday_map(filters: Filters) -> dict[str, list[dict]]:
	"""
	Returns a dict of holidays falling in the filter month and year
	with list name as key and list of holidays as values like
	{
	        'Holiday List 1': [
	                {'day_of_month': '0' , 'weekly_off': 1},
	                {'day_of_month': '1', 'weekly_off': 0}
	        ],
	        'Holiday List 2': [
	                {'day_of_month': '0' , 'weekly_off': 1},
	                {'day_of_month': '1', 'weekly_off': 0}
	        ]
	}
	"""
	# add default holiday list too
	holiday_lists = frappe.db.get_all("Holiday List", pluck="name")
	default_holiday_list = frappe.get_cached_value("Company", filters.company, "default_holiday_list")
	holiday_lists.append(default_holiday_list)

	holiday_map = frappe._dict()
	Holiday = frappe.qb.DocType("Holiday")

	holiday_condition = get_date_condition(Holiday.holiday_date, filters)

	for d in holiday_lists:
		if not d:
			continue

		holidays = (
			frappe.qb.from_(Holiday)
			.select(Holiday.holiday_date, Holiday.weekly_off)
			.where((Holiday.parent == d) & (holiday_condition))
		).run(as_dict=True)
		holiday_map.setdefault(d, holidays)

	return holiday_map


def get_rows(
	employee_details: dict, filters: Filters, holiday_map: dict, attendance_map: dict, notes: dict
) -> list[dict]:
	records = []
	default_holiday_list = frappe.get_cached_value("Company", filters.company, "default_holiday_list")

	for employee, details in employee_details.items():
		emp_holiday_list = details.holiday_list or default_holiday_list
		holidays = holiday_map.get(emp_holiday_list)

		if filters.summarized_view:
			totals = get_totals(employee, filters)
			if not any(totals.values()):
				continue

			records.append({"employee": employee, "employee_name": details.employee_name, **totals})
		else:
			employee_attendance = attendance_map.get(employee)
			if not employee_attendance:
				continue

			attendance_for_employee = get_attendance_status_for_detailed_view(
				employee, filters, employee_attendance, holidays, notes
			)
			# set employee details in the first row
			for record in attendance_for_employee:
				record.update({"employee": employee, "employee_name": details.employee_name})

			records.extend(attendance_for_employee)

	return records


def get_attendance_status_for_detailed_view(
	employee: str, filters: Filters, employee_attendance: dict, holidays: list, notes: dict
) -> list[dict]:
	"""Returns list of shift-wise attendance status for employee
	[
	        {'shift': 'Morning Shift', 1: 'A', 2: 'P', 3: 'A'....},
	        {'shift': 'Evening Shift', 1: 'P', 2: 'A', 3: 'P'....}
	]
	"""
	total_days = get_dates_in_period(filters)
	attendance_values = []

	for shift, status_dict in employee_attendance.items():
		# the marks ride along the row rather than in a column of their own: the cell keeps
		# the plain letters, which is what an export of the report should carry
		row = {"shift": shift, "marks": {}}
		"""{
	            'Morning Shift': {1: 'Present', 2: 'Absent', ...}
	            'Evening Shift': {1: 'Absent', 2: 'Present', ...}
	    },"""
		for d in total_days:
			d = getdate(d)

			status = status_dict.get(d)

			# a holiday of an unapproved day is as unhanded as the rest of it
			if status is None and holidays and is_approved(filters, employee, d):
				status = get_holiday_status(d, holidays)

			fieldname = d.strftime("%d-%m-%Y")
			row[fieldname] = get_mark(status)
			row["marks"][fieldname] = {
				"color": get_mark_color(status),
				"note": notes.get((employee, d)),
			}

		attendance_values.append(row)

	return attendance_values


def get_holiday_status(holiday_date: date, holidays: list) -> str:
	status = None
	if holidays:
		for holiday in holidays:
			if holiday_date == holiday.get("holiday_date"):
				if holiday.get("weekly_off"):
					status = "Weekly Off"
				else:
					status = "Holiday"
				break
	return status


def get_totals(employee: str, filters: Filters) -> dict[str, float]:
	"""The totals of the summarized view, read the way the attendance sheet page reads them.

	A half day is half a day of presence plus half of whatever the other half was, and
	absence counts only what payroll treats as unpaid — a leave of any kind is a leave.
	The hours are the ones entered on the attendance itself.
	"""
	approved = get_approved_condition(employee, filters)
	if approved is None:
		return dict.fromkeys(get_total_fields(), 0.0)

	Attendance = frappe.qb.DocType("Attendance")
	half_day = Attendance.status == "Half Day"

	def days(condition, weight: float = 1) -> Sum:
		"""The days matching a condition, each one worth `weight` of a day."""
		return Sum(frappe.qb.terms.Case().when(condition, weight).else_(0))

	def other_half(status: str):
		return half_day & (Attendance.half_day_status == status)

	totals = (
		frappe.qb.from_(Attendance)
		.select(
			(
				days(Attendance.status.isin(["Present", "Work From Home"]))
				+ days(half_day, 0.5)
				+ days(other_half("Present"), 0.5)
			).as_("total_present"),
			days(Attendance.status == "On Leave").as_("total_leave"),
			days(Attendance.status == "Sick Leave").as_("total_sick"),
			(days(Attendance.status == "Absent") + days(other_half("Absent"), 0.5)).as_("total_absent"),
			Sum(Attendance.overtime_hours).as_("overtime_hours"),
			Sum(Attendance.shortfall_hours).as_("shortfall_hours"),
		)
		.where(
			(Attendance.docstatus == 1)
			& (Attendance.employee == employee)
			& (Attendance.company.isin(filters.companies))
			& get_date_condition(Attendance.attendance_date, filters)
			& approved
		)
	).run(as_dict=True)

	return {field: flt(value) for field, value in totals[0].items()}


def get_approved_condition(employee: str, filters: Filters) -> Criterion | None:
	"""The days of this employee the totals may count, None if the period holds none.

	The summarized view sums the same days the detailed one prints, so the two cannot
	disagree about what has been handed over.
	"""
	Attendance = frappe.qb.DocType("Attendance")
	periods = filters.approved_periods.get(employee)

	if not periods:
		return None

	return Criterion.any(
		(Attendance.attendance_date >= start) & (Attendance.attendance_date <= end) for start, end in periods
	)


def get_total_fields() -> list[str]:
	return [column["fieldname"] for column in get_columns_for_totals()]


@frappe.whitelist()
def get_attendance_years() -> str:
	"""Returns all the years for which attendance records exist"""
	Attendance = frappe.qb.DocType("Attendance")
	year_list = (
		frappe.qb.from_(Attendance).select(Extract("year", Attendance.attendance_date).as_("year")).distinct()
	).run(as_dict=True)

	if year_list:
		year_list.sort(key=lambda d: d.year, reverse=True)
	else:
		year_list = [frappe._dict({"year": getdate().year})]

	return "\n".join(cstr(entry.year) for entry in year_list)


def get_chart_data(attendance_map: dict, filters: Filters) -> dict:
	days = get_columns_for_days(filters)
	labels = []
	absent = []
	present = []
	leave = []

	for day in days:
		labels.append(day["label"])
		total_absent_on_day = total_leaves_on_day = total_present_on_day = 0

		for __, attendance_dict in attendance_map.items():
			for __, attendance in attendance_dict.items():
				attendance_on_day = attendance.get(getdate(day["fieldname"], parse_day_first=True))

				if attendance_on_day == "On Leave":
					# leave should be counted only once for the entire day
					total_leaves_on_day += 1
					break
				elif attendance_on_day == "Absent":
					total_absent_on_day += 1
				elif attendance_on_day in ["Present", "Work From Home"]:
					total_present_on_day += 1
				elif attendance_on_day == "Half Day":
					total_present_on_day += 0.5
					total_leaves_on_day += 0.5

		absent.append(total_absent_on_day)
		present.append(total_present_on_day)
		leave.append(total_leaves_on_day)

	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("Absent"), "values": absent},
				{"name": _("Present"), "values": present},
				{"name": _("Leave"), "values": leave},
			],
		},
		"type": "line",
		"colors": ["red", "green", "blue"],
	}
