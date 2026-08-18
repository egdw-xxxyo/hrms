# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import format_date, get_link_to_form, getdate


class AttendanceSheetApproval(Document):
	def validate(self):
		self.validate_dates()
		self.validate_overlap()

	def validate_dates(self):
		if getdate(self.from_date) > getdate(self.to_date):
			frappe.throw(_("From Date cannot be after To Date"))

	def validate_overlap(self):
		"""A period may only be approved once per company: a second sheet over the same
		days would leave it unclear which of the two the payroll is supposed to read.

		Companies are counted apart, since a manager may report over employees of more
		than one and hands each of them over on its own.
		"""
		overlapping = frappe.db.get_value(
			"Attendance Sheet Approval",
			{
				"name": ("!=", self.name),
				"docstatus": 1,
				"manager": self.manager,
				"company": self.company,
				"from_date": ("<=", self.to_date),
				"to_date": (">=", self.from_date),
			},
		)

		if overlapping:
			frappe.throw(
				_("{0} already covers a part of this period for {1}").format(
					get_link_to_form("Attendance Sheet Approval", overlapping), self.company
				),
				title=_("Period Already Approved"),
			)


def get_approval_for(employee: str, date: str) -> str | None:
	"""Returns the submitted sheet that holds the day for this employee, if any.

	A day inside an approved sheet is what the accounting has already been handed,
	so the timesheet must not let it change underneath them.
	"""
	Approval = frappe.qb.DocType("Attendance Sheet Approval")
	Row = frappe.qb.DocType("Attendance Sheet Approval Employee")

	approval = (
		frappe.qb.from_(Approval)
		.join(Row)
		.on(Row.parent == Approval.name)
		.select(Approval.name)
		.where(
			(Approval.docstatus == 1)
			& (Approval.from_date <= getdate(date))
			& (Approval.to_date >= getdate(date))
			& (Row.employee == employee)
		)
		.limit(1)
	).run(pluck=True)

	return approval[0] if approval else None


def validate_not_approved(employee: str, date: str) -> None:
	approval = get_approval_for(employee, date)
	if not approval:
		return

	frappe.throw(
		_("{0} was already approved for {1}: {2}").format(
			format_date(date), employee, get_link_to_form("Attendance Sheet Approval", approval)
		),
		title=_("Period Already Approved"),
	)
