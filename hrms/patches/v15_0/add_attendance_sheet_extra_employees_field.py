from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Employee": [
				{
					"fieldname": "attendance_sheet_section",
					"fieldtype": "Section Break",
					"label": _("Attendance Sheet"),
					"insert_after": "default_shift",
				},
				{
					"description": _(
						"Employees this one fills the attendance sheet for on top of their direct reports. "
						"They are listed first in the sheet."
					),
					"fieldname": "attendance_sheet_extra_employees",
					"fieldtype": "Table MultiSelect",
					"ignore_user_permissions": 1,
					"label": _("Additional Employees in Attendance Sheet"),
					"options": "Attendance Sheet Extra Employee",
					"insert_after": "attendance_sheet_section",
				},
			]
		},
		ignore_validate=True,
	)
