// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

const METHOD = "hrms.hr.page.attendance_sheet.attendance_sheet";

const ABBR_CONTEXT = "Attendance Sheet Abbreviation";

const DAY_CONTEXT = "Day of Week Abbreviation";

// "Leave" on its own reads as the verb in several languages
const LEAVE_CONTEXT = "Attendance Sheet";

// keyed by the untranslated status, since the cell text is localized
const STATUS_META = {
	Present: { abbr: "P", color: "green" },
	"Work From Home": { abbr: "WFH", color: "green" },
	Absent: { abbr: "A", color: "red" },
	"Sick Leave": { abbr: "SL", color: "#8B5CF6" },
	"Half Day": { abbr: "HD", color: "orange" },
	"On Leave": { abbr: "L", color: "#3187D8" },
	Holiday: { abbr: "H", color: "#878787" },
	"Weekly Off": { abbr: "WO", color: "#878787" },
};

const ATTENDANCE_STATUSES = ["Present", "Work From Home", "Half Day", "Absent", "Sick Leave"];

// the statuses worth a single click on a whole day, the rest go through the dialog
const QUICK_STATUSES = ["Present", "Work From Home", "Absent", "Sick Leave"];

const DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const MENU_CLASS = "attendance-sheet-menu";

const STYLE_ID = "attendance-sheet-styles";

// the menu lives on document.body, outside any one sheet: whoever opened it says
// here what to undo once it goes away
let menu_on_close = null;

frappe.pages["attendance-sheet"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Attendance Sheet"),
		single_column: true,
	});

	frappe.breadcrumbs.add("HR");
	inject_styles();

	wrapper.attendance_sheet = new AttendanceSheet(page);
};

frappe.pages["attendance-sheet"].on_page_show = function (wrapper) {
	wrapper.attendance_sheet && wrapper.attendance_sheet.refresh();
};

class AttendanceSheet {
	constructor(page) {
		this.page = page;
		this.sheet = null;
		this.selection = null;

		this.make_filters();
		this.make_body();
		this.bind_events();
		this.refresh();
	}

	// ---------------------------------------------------------------- layout

	make_filters() {
		const today = frappe.datetime.str_to_obj(frappe.datetime.get_today());

		this.month = this.page.add_field({
			fieldtype: "Select",
			fieldname: "month",
			label: __("Month"),
			options: get_month_options(),
			default: String(today.getMonth() + 1),
			change: () => this.refresh(),
		});

		this.year = this.page.add_field({
			fieldtype: "Select",
			fieldname: "year",
			label: __("Year"),
			options: Array.from({ length: 5 }, (_, offset) =>
				String(today.getFullYear() - offset),
			),
			default: String(today.getFullYear()),
			change: () => this.refresh(),
		});

		this.company = this.page.add_field({
			fieldtype: "Select",
			fieldname: "company",
			label: __("Company"),
			options: [],
			change: () => this.refresh(),
		});

		this.summarized = this.page.add_field({
			fieldtype: "Check",
			fieldname: "summarized",
			label: __("Summarized View"),
			change: () => this.render(),
		});

		// set_input rather than set_value: the defaults must not fire a refresh of
		// their own before the page has asked for its data once
		[this.month, this.year].forEach((field) => field.set_input(field.df.default));
	}

	make_body() {
		this.$container = $('<div class="attendance-sheet-container"></div>').appendTo(
			this.page.main,
		);
		this.$legend = $('<div class="attendance-sheet-legend"></div>').appendTo(this.$container);
		this.$table = $('<div class="attendance-sheet-table"></div>').appendTo(this.$container);
	}

	bind_events() {
		this.$table.on("mousedown", "td.day", (e) => this.start_selection(e));
		this.$table.on("mouseover", "td.day", (e) => this.extend_selection(e));

		// the header drags the same way, only it covers every row at once
		this.$table.on("mousedown", "th.day", (e) => this.start_column_selection(e));
		this.$table.on("mouseover", "th.day", (e) => this.extend_column_selection(e));

		this.$table.on("mouseenter", "td.day", (e) => this.paint_related(e.currentTarget));
		this.$table.on("mouseleave", "td.day", () => this.clear_related());

		// the release may happen anywhere, a drag that leaves the table still ends here
		$(document).on("mouseup.attendance-sheet", (e) => this.finish_selection(e));
	}

	// ------------------------------------------------------------------ data

	get period() {
		const year = cint(this.year.get_value());
		const month = cint(this.month.get_value());
		const last_day = new Date(year, month, 0).getDate();
		const pad = (value) => String(value).padStart(2, "0");

		return {
			from_date: `${year}-${pad(month)}-01`,
			to_date: `${year}-${pad(month)}-${pad(last_day)}`,
		};
	}

	async refresh() {
		// the page loads and shows in the same breath, and a filter default may land
		// on top of that: one fetch is enough for all of them
		if (this.loading) return;
		this.loading = true;

		try {
			await this.load();
		} finally {
			this.loading = false;
		}
	}

	async load() {
		if (!this.company.get_value()) {
			const companies = await frappe.xcall(`${METHOD}.get_companies`);
			this.company.df.options = companies;
			this.company.refresh();
			this.company.set_input(companies[0]);
		}

		const { from_date, to_date } = this.period;
		this.sheet = await frappe.xcall(`${METHOD}.get_sheet`, {
			company: this.company.get_value(),
			from_date,
			to_date,
		});

		this.render();
	}

	get is_locked() {
		return !!(this.sheet && this.sheet.approval);
	}

	// --------------------------------------------------------------- render

	render() {
		// the table is rebuilt from scratch, so any live selection points at nodes
		// that are about to be dropped
		this.selection = null;

		this.render_actions();
		this.render_legend();

		if (!this.sheet || !this.sheet.employees.length) {
			this.$table.html(
				`<div class="text-muted attendance-sheet-empty">${__(
					"You have no direct reports to fill the timesheet for",
				)}</div>`,
			);
			return;
		}

		this.$table.html(
			this.summarized.get_value() ? this.get_summary_html() : this.get_sheet_html(),
		);
	}

	render_actions() {
		const approval = this.sheet && this.sheet.approval;

		this.page.clear_primary_action();
		this.page.clear_secondary_action();
		this.page.clear_indicator();

		if (approval) {
			this.page.set_indicator(__("Approved"), "green");
			this.page.set_secondary_action(__("Reopen Period"), () => this.reopen(approval.name));
			return;
		}

		this.page.set_indicator(__("Draft"), "orange");

		if (this.sheet && this.sheet.can_approve && this.sheet.employees.length)
			this.page.set_primary_action(__("Approve"), () => this.approve(), "check");
	}

	render_legend() {
		const entries = Object.keys(STATUS_META).map(
			(status) => `
				<span class="attendance-sheet-legend-item" style="border-left-color:${STATUS_META[status].color}">
					${__(status)} - ${get_abbr(status)}
				</span>`,
		);

		entries.push(`
			<span class="attendance-sheet-legend-item" style="border-left-color:green">
				${__("Overtime Hours")} - +2
			</span>
			<span class="attendance-sheet-legend-item" style="border-left-color:red">
				${__("Shortfall Hours")} - -2
			</span>`);

		this.$legend.html(entries.join(""));
	}

	get_sheet_html() {
		const head = this.sheet.dates
			.map((date) => `<th class="day" data-date="${date}">${get_day_label(date)}</th>`)
			.join("");

		const body = this.sheet.employees
			.map(
				(row) => `
					<tr data-employee="${row.employee}">
						${get_employee_html(row)}
						${this.sheet.dates.map((date) => get_cell_html(row, date)).join("")}
					</tr>`,
			)
			.join("");

		return `
			<table class="attendance-sheet">
				<thead><tr><th class="employee">${__("Employee")}</th>${head}</tr></thead>
				<tbody>${body}</tbody>
			</table>`;
	}

	get_summary_html() {
		// day counts, not the statuses themselves: "Leave" alone translates as a verb
		const columns = [
			__("Employee"),
			__("Present Days"),
			__("Leave Days"),
			__("Sick Days"),
			__("Absent Days"),
			__("Overtime Hours"),
			__("Shortfall Hours"),
		];

		const body = this.sheet.employees
			.map((row) => {
				const totals = get_totals(row);
				return `
					<tr>
						${get_employee_html(row)}
						<td class="number">${totals.present}</td>
						<td class="number">${totals.leave}</td>
						<td class="number">${totals.sick}</td>
						<td class="number">${totals.absent}</td>
						<td class="number">${totals.overtime}</td>
						<td class="number">${totals.shortfall}</td>
					</tr>`;
			})
			.join("");

		return `
			<table class="attendance-sheet attendance-sheet--summary">
				<thead><tr>${columns
					.map(
						(label, index) =>
							`<th class="${index ? "number" : "employee"}">${label}</th>`,
					)
					.join("")}</tr></thead>
				<tbody>${body}</tbody>
			</table>`;
	}

	// ------------------------------------------------------------ selection

	get_cell(employee, date) {
		const row = this.sheet.employees.find((entry) => entry.employee === employee);
		return row ? row.days[date] : null;
	}

	// a cell is addressed by its place in the grid, so a drag can span rows as well
	// as days and the header is just a drag that covers every row
	get_position(cell) {
		return {
			row: this.sheet.employees.findIndex((row) => row.employee === cell.dataset.employee),
			column: this.sheet.dates.indexOf(cell.dataset.date),
		};
	}

	get bounds() {
		const { anchor, focus } = this.selection;
		const [top, bottom] = [anchor.row, focus.row].sort((a, b) => a - b);
		const [left, right] = [anchor.column, focus.column].sort((a, b) => a - b);

		return { top, bottom, left, right };
	}

	start_selection(e) {
		if (e.button !== 0 || this.is_locked) return;

		e.preventDefault();
		close_menu();

		const position = this.get_position(e.currentTarget);
		this.selection = { anchor: position, focus: position, dragging: true, columns: false };
		this.paint_selection();
	}

	extend_selection(e) {
		if (!this.selection || !this.selection.dragging || this.selection.columns) return;

		this.selection.focus = this.get_position(e.currentTarget);
		this.paint_selection();
	}

	start_column_selection(e) {
		if (e.button !== 0 || this.is_locked || this.summarized.get_value()) return;

		e.preventDefault();
		close_menu();

		const column = this.sheet.dates.indexOf(e.currentTarget.dataset.date);
		this.selection = {
			anchor: { row: 0, column },
			focus: { row: this.sheet.employees.length - 1, column },
			dragging: true,
			columns: true,
		};
		this.paint_selection();
	}

	extend_column_selection(e) {
		if (!this.selection || !this.selection.dragging || !this.selection.columns) return;

		const column = this.sheet.dates.indexOf(e.currentTarget.dataset.date);
		if (column === -1) return;

		this.selection.focus = { row: this.sheet.employees.length - 1, column };
		this.paint_selection();
	}

	finish_selection(e) {
		if (!this.selection || !this.selection.dragging) return;

		// the drag is over, but the rectangle stays painted: it is what the menu about
		// to open acts on, and it is cleared when that menu closes
		this.selection.dragging = false;

		const { employees, dates } = this.get_selection();
		if (!employees.length || !dates.length) return this.clear_selection();

		if (!this.open_selection_menu(e, employees, dates)) this.clear_selection();
	}

	get_selection() {
		const { top, bottom, left, right } = this.bounds;

		return {
			employees: this.sheet.employees.slice(top, bottom + 1).map((row) => row.employee),
			dates: this.sheet.dates.slice(left, right + 1),
		};
	}

	paint_selection() {
		const { top, bottom, left, right } = this.bounds;

		// the braces matter: jQuery stops iterating on a callback that returns false,
		// and classList.toggle answers with whether the class ended up set
		this.$table.find("td.day").each((_index, cell) => {
			const { row, column } = this.get_position(cell);
			cell.classList.toggle(
				"selected",
				row >= top && row <= bottom && column >= left && column <= right,
			);
		});

		this.$table.find("th.day").each((_index, header) => {
			const column = this.sheet.dates.indexOf(header.dataset.date);
			header.classList.toggle(
				"selected",
				this.selection.columns && column >= left && column <= right,
			);
		});
	}

	clear_selection() {
		if (!this.selection) return;

		this.$table.find(".selected").removeClass("selected");
		this.selection = null;
	}

	// ------------------------------------------------------------- related

	paint_related(cell) {
		// a drag is already painting; two highlights at once only muddle the range
		if (this.selection && this.selection.dragging) return;

		const group = cell.dataset.group;
		if (!group) return;

		this.$table.find(`td.day[data-group="${CSS.escape(group)}"]`).addClass("related");
	}

	clear_related() {
		this.$table.find("td.day.related").removeClass("related");
	}

	// ---------------------------------------------------------------- menus

	/** One menu for every selection: a day of one employee and a month of the whole
	 * team differ only in what the same entries are applied to. */
	open_selection_menu(event, employees, dates) {
		const period = { from_date: dates[0], to_date: dates[dates.length - 1] };
		const cell =
			employees.length === 1 && dates.length === 1
				? this.get_cell(employees[0], dates[0])
				: null;

		// an approved day is read-only, but its documents stay reachable
		if (cell && cell.locked)
			return show_menu(event, this.get_document_items(cell), () => this.clear_selection());

		const items = QUICK_STATUSES.map((status) => ({
			label: __(status),
			onclick: () => this.save_attendance({ employees, ...period, status }),
		}));

		items.push({
			label: __("More Options") + "…",
			onclick: () => this.open_attendance_dialog({ employees, ...period, cell }),
		});

		// a leave application belongs to one employee, a whole column cannot be one
		if (employees.length === 1)
			items.push({
				label: __("Leave", null, LEAVE_CONTEXT),
				onclick: () =>
					this.open_leave_dialog({
						employee: employees[0],
						name: cell && cell.leave_application,
						...period,
					}),
			});

		items.push({
			label: __("Delete"),
			onclick: () => this.clear_range(employees, period),
		});

		// clearing a range keeps a leave that reaches outside it, so the leave itself
		// needs a way out
		if (cell && cell.leave_application)
			items.push({
				label: __("Delete Leave"),
				onclick: () => this.delete_leave(cell),
			});

		return show_menu(event, items.concat(this.get_document_items(cell)), () =>
			this.clear_selection(),
		);
	}

	get_document_items(cell) {
		if (!cell) return [];

		const items = [];

		if (cell.attendance)
			items.push({
				label: __("Open Attendance"),
				onclick: () => frappe.set_route("Form", "Attendance", cell.attendance),
			});

		if (cell.leave_application)
			items.push({
				label: __("Open Leave"),
				onclick: () =>
					frappe.set_route("Form", "Leave Application", cell.leave_application),
			});

		return items;
	}

	// -------------------------------------------------------------- actions

	delete_leave(cell) {
		frappe.confirm(__("Delete this leave?"), async () => {
			await frappe.xcall(`${METHOD}.delete_leave`, { name: cell.leave_application });
			frappe.show_alert({ message: __("Deleted"), indicator: "green" });
			this.refresh();
		});
	}

	clear_range(employees, period) {
		const span =
			period.from_date === period.to_date
				? frappe.datetime.str_to_user(period.from_date)
				: `${frappe.datetime.str_to_user(
						period.from_date,
				  )} — ${frappe.datetime.str_to_user(period.to_date)}`;

		const message =
			employees.length === 1
				? __("Delete everything marked for {0} on {1}?", [
						this.employee_name(employees[0]),
						span,
				  ])
				: __("Delete everything marked for {0} employees on {1}?", [
						employees.length,
						span,
				  ]);

		frappe.confirm(message, async () => {
			const result = await frappe.xcall(`${METHOD}.clear_range`, {
				company: this.company.get_value(),
				employees,
				...period,
			});

			report_removal(result);
			this.refresh();
		});
	}

	async save_attendance(values) {
		const result = await frappe.xcall(`${METHOD}.save_attendance`, {
			company: this.company.get_value(),
			...values,
		});

		report_result(result);
		this.refresh();

		return result;
	}

	approve() {
		frappe.confirm(
			__(
				"Approve the timesheet for this period? The days it covers can no longer be changed.",
			),
			async () => {
				const { from_date, to_date } = this.period;
				await frappe.xcall(`${METHOD}.approve_sheet`, {
					company: this.company.get_value(),
					from_date,
					to_date,
				});
				frappe.show_alert({ message: __("Timesheet approved"), indicator: "green" });
				this.refresh();
			},
		);
	}

	reopen(name) {
		frappe.confirm(__("Reopen this period for changes?"), async () => {
			await frappe.xcall(`${METHOD}.cancel_approval`, { name });
			frappe.show_alert({ message: __("Period reopened"), indicator: "green" });
			this.refresh();
		});
	}

	// ------------------------------------------------------------- dialogs

	open_attendance_dialog({ employees, from_date, to_date, cell }) {
		const is_range = from_date !== to_date || employees.length > 1;
		const target =
			employees.length === 1
				? this.employee_name(employees[0])
				: __("{0} employees", [employees.length]);

		const dialog = new frappe.ui.Dialog({
			title: cell ? __("Edit Attendance") : __("Mark Attendance"),
			fields: get_attendance_fields(is_range),
			primary_action_label: cell ? __("Update") : __("Mark"),
			primary_action: async (values) => {
				dialog.disable_primary_action();
				try {
					await this.save_attendance({
						employees,
						from_date,
						to_date,
						status: values.status,
						half_day_status: values.half_day_status,
						overtime_hours: values.overtime_hours,
						shortfall_hours: values.shortfall_hours,
						shift: values.shift,
					});
					dialog.hide();
				} finally {
					dialog.enable_primary_action();
				}
			},
		});

		dialog.set_values({
			target,
			period: is_range
				? `${frappe.datetime.str_to_user(from_date)} — ${frappe.datetime.str_to_user(
						to_date,
				  )}`
				: frappe.datetime.str_to_user(from_date),
			status: (cell && cell.status) || "Present",
			half_day_status: (cell && cell.half_day_status) || "Absent",
			overtime_hours: (cell && cell.overtime_hours) || 0,
			shortfall_hours: (cell && cell.shortfall_hours) || 0,
			shift: (cell && cell.shift) || "",
		});

		dialog.show();
	}

	employee_name(employee) {
		const row = this.sheet.employees.find((entry) => entry.employee === employee);
		return row ? row.employee_name : employee;
	}

	async open_leave_dialog({ employee, name, from_date, to_date }) {
		const doc = name ? await frappe.xcall(`${METHOD}.get_leave`, { name }) : null;
		const start = doc ? doc.from_date : from_date;

		const details = await frappe.xcall(`${METHOD}.get_leave_details`, {
			employee,
			date: start,
		});

		const allocations = details.leave_allocation || {};
		// leaves without pay have no allocation, so they are listed separately
		const allowed_types = Object.keys(allocations).concat(details.lwps || []);

		const refresh_summary = frappe.utils.debounce(
			() => update_leave_summary(dialog, employee, allocations),
			300,
		);

		const dialog = new frappe.ui.Dialog({
			title: doc ? __("Edit Leave") : __("New Leave"),
			fields: get_leave_fields(allowed_types, refresh_summary),
			primary_action_label: doc ? __("Update") : __("Create"),
			primary_action: async (values) => {
				dialog.disable_primary_action();
				try {
					await frappe.xcall(`${METHOD}.save_leave`, {
						employee,
						name: doc ? doc.name : null,
						leave_type: values.leave_type,
						from_date: values.from_date,
						to_date: values.to_date,
						half_day: values.half_day,
						half_day_date: values.half_day_date,
						description: values.description,
					});
					dialog.hide();
					frappe.show_alert({
						message: doc ? __("Leave updated") : __("Leave created"),
						indicator: "green",
					});
					this.refresh();
				} finally {
					dialog.enable_primary_action();
				}
			},
		});

		dialog.set_values({
			employee_name: this.employee_name(employee),
			leave_type: doc ? doc.leave_type : "",
			from_date: start,
			to_date: doc ? doc.to_date : to_date,
			half_day: doc ? doc.half_day : 0,
			half_day_date: doc ? doc.half_day_date : "",
			description: doc ? doc.description : "",
		});

		dialog.show();
		refresh_summary();
	}
}

// -------------------------------------------------------------------- cells

function get_employee_html(row) {
	const name = frappe.utils.escape_html(row.employee_name || row.employee);

	return `
		<td class="employee">
			<a href="/app/employee/${encodeURIComponent(row.employee)}" title="${row.employee}"
				>${name}</a>
		</td>`;
}

function get_cell_html(row, date) {
	const cell = row.days[date];
	const meta = STATUS_META[cell.status] || {};
	const classes = ["day", cell.locked ? "locked" : ""].filter(Boolean).join(" ");
	// the cells of one leave (or of one attendance) light up together on hover
	const group = cell.leave_application
		? `Leave Application:${cell.leave_application}`
		: cell.attendance
		  ? `Attendance:${cell.attendance}`
		  : "";

	return `
		<td class="${classes}" data-employee="${row.employee}" data-date="${date}"
			data-group="${frappe.utils.escape_html(group)}"
			title="${frappe.utils.escape_html(get_cell_title(cell))}">
			<span class="status" style="color:${meta.color || "#878787"}">${get_cell_abbr(cell)}</span>
			${get_hours_html(cell)}
		</td>`;
}

function get_cell_abbr(cell) {
	if (!cell.status) return "&nbsp;";
	if (cell.status !== "Half Day") return get_abbr(cell.status);

	return `${get_abbr("Half Day")}/${get_abbr(
		cell.half_day_status === "Present" ? "Present" : "Absent",
	)}`;
}

function get_abbr(status) {
	const meta = STATUS_META[status];
	return meta ? __(meta.abbr, null, ABBR_CONTEXT) : "";
}

function get_hours_html(cell) {
	if (cell.overtime_hours)
		return `<span class="hours over">+${format_hours(cell.overtime_hours)}</span>`;
	if (cell.shortfall_hours)
		return `<span class="hours under">-${format_hours(cell.shortfall_hours)}</span>`;

	return "";
}

function format_hours(value) {
	return String(Math.round(flt(value) * 100) / 100);
}

function get_cell_title(cell) {
	return [cell.status ? __(cell.status) : "", cell.shift, cell.locked ? __("Approved") : ""]
		.filter(Boolean)
		.join(" · ");
}

function get_totals(row) {
	const totals = { present: 0, leave: 0, sick: 0, absent: 0, overtime: 0, shortfall: 0 };

	Object.values(row.days).forEach((cell) => {
		if (["Present", "Work From Home"].includes(cell.status)) totals.present += 1;
		else if (cell.status === "On Leave") totals.leave += 1;
		else if (cell.status === "Sick Leave") totals.sick += 1;
		else if (cell.status === "Absent") totals.absent += 1;
		else if (cell.status === "Half Day") {
			totals.present += 0.5;
			if (cell.half_day_status === "Present") totals.present += 0.5;
			else totals.absent += 0.5;
		}

		totals.overtime += flt(cell.overtime_hours);
		totals.shortfall += flt(cell.shortfall_hours);
	});

	return Object.fromEntries(
		Object.entries(totals).map(([key, value]) => [key, format_hours(value)]),
	);
}

// month names come from the locale rather than from the translation table: the
// Select control translates its own labels, and "May" as a word translates into
// the modal verb rather than into the month
function get_month_options() {
	const format = new Intl.DateTimeFormat(frappe.boot.lang || "en", { month: "long" });

	return Array.from({ length: 12 }, (_, index) => {
		const label = format.format(new Date(2000, index, 1));
		return {
			label: label.charAt(0).toUpperCase() + label.slice(1),
			value: String(index + 1),
		};
	});
}

function get_day_label(date) {
	const day = frappe.datetime.str_to_obj(date);
	// getDay() counts from Sunday, the abbreviations from Monday
	const abbr = DAY_ABBR[(day.getDay() + 6) % 7];

	return `${day.getDate()} ${__(abbr, null, DAY_CONTEXT)}`;
}

// ------------------------------------------------------------------ dialogs

function get_attendance_fields(is_range) {
	return [
		{ fieldtype: "Data", fieldname: "target", label: __("Employee"), read_only: 1 },
		{ fieldtype: "Column Break" },
		{ fieldtype: "Data", fieldname: "period", label: __("Period"), read_only: 1 },
		{ fieldtype: "Section Break" },
		{
			fieldtype: "Select",
			fieldname: "status",
			label: __("Status"),
			options: ATTENDANCE_STATUSES,
			reqd: 1,
		},
		{
			fieldtype: "Select",
			fieldname: "half_day_status",
			label: __("Status for Other Half"),
			options: ["Present", "Absent"],
			depends_on: "eval:doc.status == 'Half Day'",
		},
		{ fieldtype: "Column Break" },
		{ fieldtype: "Link", fieldname: "shift", label: __("Shift"), options: "Shift Type" },
		{ fieldtype: "Section Break" },
		{
			fieldtype: "Float",
			fieldname: "overtime_hours",
			label: __("Overtime Hours"),
			precision: 2,
			description: is_range ? __("Applied to every day of the period") : "",
		},
		{ fieldtype: "Column Break" },
		{
			fieldtype: "Float",
			fieldname: "shortfall_hours",
			label: __("Shortfall Hours"),
			precision: 2,
		},
	];
}

function get_leave_fields(allowed_types, refresh_summary) {
	return [
		{ fieldtype: "Data", fieldname: "employee_name", label: __("Employee"), read_only: 1 },
		{ fieldtype: "Column Break" },
		{
			fieldtype: "Link",
			fieldname: "leave_type",
			label: __("Leave Type"),
			options: "Leave Type",
			reqd: 1,
			get_query: () => ({ filters: [["leave_type_name", "in", allowed_types]] }),
			onchange: refresh_summary,
		},
		{ fieldtype: "Section Break" },
		{
			fieldtype: "Date",
			fieldname: "from_date",
			label: __("From Date"),
			reqd: 1,
			onchange: refresh_summary,
		},
		{
			fieldtype: "Date",
			fieldname: "to_date",
			label: __("To Date"),
			reqd: 1,
			onchange: refresh_summary,
		},
		{ fieldtype: "Column Break" },
		{
			fieldtype: "Check",
			fieldname: "half_day",
			label: __("Half Day"),
			onchange: refresh_summary,
		},
		{
			fieldtype: "Date",
			fieldname: "half_day_date",
			label: __("Half Day Date"),
			depends_on: "half_day",
			onchange: refresh_summary,
		},
		{ fieldtype: "Section Break" },
		{ fieldtype: "HTML", fieldname: "summary" },
		{ fieldtype: "Small Text", fieldname: "description", label: __("Reason") },
	];
}

async function update_leave_summary(dialog, employee, allocations) {
	const values = dialog.get_values(true);

	if (values.half_day && !values.half_day_date) {
		dialog.set_value("half_day_date", values.from_date);
		return;
	}

	const allocation = allocations[values.leave_type];
	const days =
		values.leave_type && values.from_date && values.to_date
			? await frappe.xcall(
					"hrms.hr.doctype.leave_application.leave_application.get_number_of_leave_days",
					{
						employee,
						leave_type: values.leave_type,
						from_date: values.from_date,
						to_date: values.to_date,
						half_day: values.half_day,
						half_day_date: values.half_day_date,
					},
			  )
			: null;

	const summary = [
		days === null ? null : [__("Days"), days],
		allocation ? [__("Balance"), allocation.remaining_leaves] : null,
	]
		.filter(Boolean)
		.map(([label, value]) => `<span class="mr-4 text-muted">${label}: <b>${value}</b></span>`)
		.join("");

	dialog.fields_dict.summary.$wrapper.html(summary);
}

function report_removal({ deleted, skipped }) {
	if (deleted)
		frappe.show_alert({
			message: __("{0} record(s) deleted", [deleted]),
			indicator: "green",
		});
	else if (!skipped.length)
		frappe.show_alert({ message: __("There was nothing to delete"), indicator: "blue" });

	if (skipped.length)
		report_skipped(skipped, deleted ? "orange" : "red", __("Days that were kept"));
}

// a period is a set of records, so the days that could not be marked are listed
// instead of failing the whole period
function report_result({ created, skipped }) {
	if (created.length)
		frappe.show_alert({
			message: __("{0} day(s) marked", [created.length]),
			indicator: "green",
		});

	if (!skipped.length) return;

	report_skipped(skipped, created.length ? "orange" : "red", __("Days that were skipped"));
}

function report_skipped(skipped, indicator, title) {
	frappe.msgprint({
		title,
		message: [[__("Employee"), __("Date"), __("Reason")]].concat(
			skipped.map((day) => [day.employee, day.date, day.reason]),
		),
		as_table: true,
		indicator,
	});
}

// -------------------------------------------------------------------- menus

function show_menu(event, items, on_close) {
	close_menu();

	if (!items.length) return false;

	menu_on_close = on_close || null;

	const $menu = $(`<div class="${MENU_CLASS}"></div>`);
	items.forEach((item) =>
		$(`<div class="${MENU_CLASS}-item"></div>`)
			.text(item.label)
			.on("click", () => {
				close_menu();
				item.onclick();
			})
			.appendTo($menu),
	);

	$menu.appendTo(document.body);
	position_menu($menu, event);

	// the click that opens the menu must not close it again
	setTimeout(() => {
		$(document).on(`mousedown.${MENU_CLASS}`, (e) => {
			if (!$(e.target).closest(`.${MENU_CLASS}`).length) close_menu();
		});
		$(document).on(`keydown.${MENU_CLASS}`, (e) => e.key === "Escape" && close_menu());
	});

	return true;
}

function position_menu($menu, event) {
	const { width, height } = $menu[0].getBoundingClientRect();
	const x = Math.min(event ? event.clientX : 0, window.innerWidth - width - 8);
	const y = Math.min(event ? event.clientY : 0, window.innerHeight - height - 8);

	$menu.css({ left: `${Math.max(8, x)}px`, top: `${Math.max(8, y)}px` });
}

function close_menu() {
	$(`.${MENU_CLASS}`).remove();
	$(document).off(`mousedown.${MENU_CLASS}`).off(`keydown.${MENU_CLASS}`);

	const on_close = menu_on_close;
	menu_on_close = null;
	on_close && on_close();
}

// -------------------------------------------------------------------- style

function inject_styles() {
	if (document.getElementById(STYLE_ID)) return;

	const style = document.createElement("style");
	style.id = STYLE_ID;
	style.textContent = `
		.attendance-sheet-container { padding: 15px 0; }
		.attendance-sheet-table { overflow-x: auto; background-color: var(--fg-color);
			border: 1px solid var(--border-color); border-radius: var(--border-radius-md); }
		.attendance-sheet-empty { padding: 30px; text-align: center; }
		table.attendance-sheet { border-collapse: separate; border-spacing: 0; width: 100%;
			font-size: var(--text-sm); user-select: none; }
		table.attendance-sheet th, table.attendance-sheet td {
			border-bottom: 1px solid var(--border-color); padding: 6px 4px;
			text-align: center; white-space: nowrap; }
		table.attendance-sheet th { position: sticky; top: 0; z-index: 2;
			background-color: var(--fg-color); color: var(--text-muted); font-weight: normal; }
		/* the name column stays readable while the days scroll under it */
		table.attendance-sheet th.employee, table.attendance-sheet td.employee {
			position: sticky; left: 0; z-index: 1; min-width: 180px; text-align: left;
			padding-left: 12px; background-color: var(--fg-color);
			border-right: 1px solid var(--border-color); }
		table.attendance-sheet th.employee { z-index: 3; }
		table.attendance-sheet td.employee a { color: var(--text-color); }
		table.attendance-sheet td.employee a:hover { color: var(--text-color);
			text-decoration: underline; }
		table.attendance-sheet td.day { min-width: 46px; cursor: pointer; }
		table.attendance-sheet th.day, table.attendance-sheet td.day,
		table.attendance-sheet th.number, table.attendance-sheet td.number {
			border-right: 1px solid var(--border-color); }
		table.attendance-sheet th:last-child, table.attendance-sheet td:last-child {
			border-right: none; }
		table.attendance-sheet th.day { cursor: pointer; }
		table.attendance-sheet th.day:hover { color: var(--text-color);
			background-color: var(--fg-hover-color); }
		table.attendance-sheet th.day.selected, table.attendance-sheet th.day.selected:hover {
			color: var(--text-color); background-color: rgba(49, 138, 216, 0.18);
			box-shadow: inset 0 0 0 1px #318AD8; }
		table.attendance-sheet td.day:hover { background-color: var(--fg-hover-color); }
		/* not --highlight-color: it resolves to a lighter grey than the hover state,
		   which leaves the dragged range invisible under the cursor */
		table.attendance-sheet td.day.selected,
		table.attendance-sheet td.day.selected:hover {
			background-color: rgba(49, 138, 216, 0.18);
			box-shadow: inset 0 0 0 1px #318AD8; }
		/* every day of the same record, lit from a hover over any one of them */
		table.attendance-sheet td.day.related { background-color: var(--fg-hover-color);
			box-shadow: inset 0 -2px 0 0 var(--gray-400, #9CA3AF); }
		table.attendance-sheet td.day.locked { cursor: default; opacity: 0.6; }
		table.attendance-sheet td.number, table.attendance-sheet th.number {
			text-align: right; padding-right: 12px; min-width: 90px; }
		.attendance-sheet .status { display: block; }
		.attendance-sheet .hours { display: block; font-size: var(--text-xs); }
		.attendance-sheet .hours.over { color: var(--green-500, green); }
		.attendance-sheet .hours.under { color: var(--red-500, red); }
		.attendance-sheet-legend { padding: 0 2px 12px; font-size: var(--text-sm);
			color: var(--text-muted); }
		.attendance-sheet-legend-item { border-left: 2px solid; padding: 0 12px 0 5px;
			margin-right: 3px; display: inline-block; }
		.${MENU_CLASS} { position: fixed; z-index: 1050; min-width: 190px; padding: 4px 0;
			background-color: var(--fg-color); border: 1px solid var(--border-color);
			border-radius: var(--border-radius-md); box-shadow: var(--shadow-md);
			font-size: var(--text-md); }
		.${MENU_CLASS}-item { padding: 6px 12px; cursor: pointer; white-space: nowrap; }
		.${MENU_CLASS}-item:hover { background-color: var(--fg-hover-color); }
	`;
	document.head.appendChild(style);
}
