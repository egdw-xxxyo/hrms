import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Leave Type": [
				{
					"description": _(
						"Shown in the attendance sheet on the days of this leave. "
						"Days of a leave without an abbreviation are marked as leave in general."
					),
					"fieldname": "attendance_sheet_abbr",
					"fieldtype": "Data",
					"label": _("Attendance Sheet Abbreviation"),
					"length": 5,
					"insert_after": "leave_type_name",
				},
			]
		},
		ignore_validate=True,
	)

	seed_abbreviations()


def seed_abbreviations():
	"""Gives the existing leave types the two abbreviations the sheet was built around.

	Only empty fields are filled, so a site that already labelled its types keeps them.
	The abbreviation is data rather than a label, so it is stored in the site language:
	a patch runs untranslated unless it is told which one that is.
	"""
	lang = frappe.db.get_default("lang") or "en"
	table = frappe.qb.DocType("Leave Type")

	for is_lwp, abbr in (
		(1, _("Unpaid", lang=lang, context="Leave Type Abbreviation")),
		(0, _("Paid", lang=lang, context="Leave Type Abbreviation")),
	):
		frappe.qb.update(table).set(table.attendance_sheet_abbr, abbr).where(
			(table.is_lwp == is_lwp)
			& (table.attendance_sheet_abbr.isnull() | (table.attendance_sheet_abbr == ""))
		).run()
