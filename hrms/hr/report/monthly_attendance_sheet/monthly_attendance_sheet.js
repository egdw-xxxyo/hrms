// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.query_reports["Monthly Attendance Sheet"] = {
	filters: [
		{
			fieldname: "filter_based_on",
			label: __("Filter Based On"),
			fieldtype: "Select",
			options: ["Month", "Date Range"],
			default: "Month",
			reqd: 1,
			on_change: (report) => {
				let filter_based_on = frappe.query_report.get_filter_value("filter_based_on");

				if (filter_based_on == "Month") {
					set_reqd_filter("month", true);
					set_reqd_filter("year", true);
					set_reqd_filter("start_date", false);
					set_reqd_filter("end_date", false);
				}
				if (filter_based_on == "Date Range") {
					set_reqd_filter("month", false);
					set_reqd_filter("year", false);
					set_reqd_filter("start_date", true);
					set_reqd_filter("end_date", true);
				}
				report.refresh();
			},
		},
		{
			fieldname: "month",
			label: __("Month"),
			fieldtype: "Select",
			options: [
				{ value: 1, label: __("Jan") },
				{ value: 2, label: __("Feb") },
				{ value: 3, label: __("Mar") },
				{ value: 4, label: __("Apr") },
				{ value: 5, label: __("May") },
				{ value: 6, label: __("June") },
				{ value: 7, label: __("July") },
				{ value: 8, label: __("Aug") },
				{ value: 9, label: __("Sep") },
				{ value: 10, label: __("Oct") },
				{ value: 11, label: __("Nov") },
				{ value: 12, label: __("Dec") },
			],
			default: frappe.datetime.str_to_obj(frappe.datetime.get_today()).getMonth() + 1,
			depends_on: "eval:doc.filter_based_on == 'Month'",
		},
		{
			fieldname: "start_date",
			label: __("Start Date"),
			fieldtype: "Date",
			depends_on: "eval:doc.filter_based_on == 'Date Range'",
			on_change: validate_date_range,
		},
		{
			fieldname: "end_date",
			label: __("End Date"),
			fieldtype: "Date",
			depends_on: "eval:doc.filter_based_on == 'Date Range'",
			on_change: validate_date_range,
		},
		{
			fieldname: "year",
			label: __("Year"),
			fieldtype: "Select",
			depends_on: "eval:doc.filter_based_on == 'Month'",
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
			get_query: () => {
				var company = frappe.query_report.get_filter_value("company");
				return {
					filters: {
						company: company,
					},
				};
			},
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["", "Branch", "Grade", "Department", "Designation", "Manager"],
		},
		{
			fieldname: "include_company_descendants",
			label: __("Include Company Descendants"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "summarized_view",
			label: __("Summarized View"),
			fieldtype: "Check",
			default: 0,
			depends_on: "eval:!doc.unsubmitted_view",
		},
		{
			fieldname: "show_chart",
			label: __("Show Chart"),
			fieldtype: "Check",
			default: 0,
			depends_on: "eval:!doc.unsubmitted_view",
		},
		{
			fieldname: "unsubmitted_view",
			label: __("Unsubmitted Sheets"),
			fieldtype: "Check",
			default: 0,
			// the report keeps one table across runs and only hands it new rows, so the
			// options it was built with — the row height among them — outlive the view
			// they were meant for. Toggling the view throws the table away instead
			on_change: (report) => {
				if (report.datatable) {
					report.datatable.destroy();
					report.datatable = null;
				}
				report.refresh();
			},
		},
	],
	onload: function (report) {
		add_export_button(report);

		return frappe.call({
			method: "hrms.hr.report.monthly_attendance_sheet.monthly_attendance_sheet.get_attendance_years",
			callback: function (r) {
				var year_filter = frappe.query_report.get_filter("year");
				year_filter.df.options = r.message;
				year_filter.df.default = r.message.split("\n")[0];
				year_filter.refresh();
				year_filter.set_input(year_filter.df.default);
			},
		});
	},
	formatter: function (value, row, column, data, default_formatter) {
		const unsubmitted = frappe.query_report.get_filter_value("unsubmitted_view");

		if (!unsubmitted && column.fieldtype === "Float") return format_total(value);

		value = default_formatter(value, row, column, data);
		const group_by = frappe.query_report.get_filter_value("group_by");

		if (group_by && column.colIndex === 1) {
			value = "<strong>" + value + "</strong>";
		}

		if (unsubmitted) return value;

		const mark = data && data.marks && data.marks[column.fieldname];

		return mark ? get_mark_html(value, mark) : value;
	},
	// a day carries two lines, the mark and the note under it, the way the sheet page
	// draws it; the stock 33 fits only one. The number has to be the real height of the
	// two, because it is also what the virtual scroll positions rows by, and the cell
	// hides whatever does not fit — see the line heights in style_days. The unsubmitted
	// view carries no days and is left at the height every other report is drawn with
	get_datatable_options: (options) =>
		frappe.query_report.get_filter_value("unsubmitted_view")
			? options
			: { ...options, cellHeight: 50, columns: stick_the_name(options.columns) },
	after_datatable_render: () => {
		// the wrapper keeps the space of the chart it last drew, so it is hidden by hand
		if (!frappe.query_report.get_filter_value("show_chart")) frappe.query_report.$chart.hide();

		// the unsubmitted view is a plain list of employees: none of the sheet's dressing
		// belongs to it, and what an earlier run left behind is taken back off
		if (frappe.query_report.get_filter_value("unsubmitted_view")) return strip_sheet_style();

		style_days();
		move_scrollbar();
		stripe_rows();
		follow_cursor();
	},
};

// the name is what a day is read back to, and a month is wider than any window: the table
// keeps that one column in place while the days scroll under it. The datatable does the
// holding itself — the column only has to say so, and it says it here rather than in the
// report's python, where a stray key would travel into every other reader of the columns
function stick_the_name(columns) {
	return (columns || []).map((column) =>
		column.fieldname === "employee_name" ? { ...column, sticky: true } : column,
	);
}

// the file is built and named by the server, so it is asked for with a form post rather
// than a call: what comes back is the workbook itself, not a payload to unpack
function add_export_button(report) {
	report.page.add_inner_button(__("Export to Excel"), () => {
		open_url_post("/api/method/erpnext.payroll_ua.attendance_export.download_report", {
			filters: JSON.stringify(frappe.query_report.get_filter_values()),
		});
	});
}

// what style_days, move_scrollbar and stripe_rows hung on the report, taken back off so
// a table that is not the sheet is drawn the way every other report is
function strip_sheet_style() {
	const report = frappe.query_report.$report[0];
	const strip = report.querySelector(".attendance-marks-scrollbar");

	frappe.query_report.$report.removeClass("attendance-marks");
	if (strip) strip.remove();
	get_sheet_style("attendance-marks-stripes").textContent = "";
	get_sheet_style("attendance-marks-cursor").textContent = "";
}

// the day cells are the sheet page's cells: the same class names, the same rules, down to
// the padding. The rest is what the datatable costs — its cell is a fixed box that hides
// what does not fit, so the two lines are given a height each and the box is cut to the
// bone (the stock half a rem all round leaves the second line under the floor). The
// filter row is not a day and keeps the height it had. Written once, reaching one table
function style_days() {
	const table_class = "attendance-marks";
	frappe.query_report.$report.addClass(table_class);

	if (document.getElementById(table_class)) return;

	const style = document.createElement("style");
	style.id = table_class;
	style.textContent = `
		.${table_class} { --sheet-zebra: #F4F4F5; --sheet-cross: rgba(49, 138, 216, 0.08);
			--sheet-off: #FFD1D1; }
		/* the tint has to carry further in the dark than in the light: the stripes are
		   already a step above the ground there, and a cursor as faint as the one the
		   light theme needs reads as just another stripe */
		[data-theme="dark"] .${table_class} { --sheet-zebra: #26262A;
			--sheet-cross: rgba(120, 180, 240, 0.22); --sheet-off: #5A2A2A; }
		/* a day nobody was meant to work. Named through the row as well, so that the rule
		   outweighs the stripes, which are written per row and land in a later sheet */
		.${table_class} .dt-row .dt-cell:has(.off) { background-color: var(--sheet-off); }
		.${table_class} .dt-cell__content { padding: 6px 4px; }
		.${table_class} .status { display: block; line-height: 1.4; }
		.${table_class} .hours { display: block; font-size: var(--text-xs); line-height: 1.2; }
		.${table_class} .hours.leave { color: var(--text-muted); }
		.${table_class} .hours.over { color: var(--green-500, green); }
		.${table_class} .hours.under { color: var(--red-500, red); }
		.${table_class} .dt-row-filter .dt-cell { height: 33px; }
		/* the name column the table holds in place has to stay opaque, or the days
		   scroll through it; the stripes and the cursor are written per row and
		   outweigh this, which is only the ground under an unstriped one */
		.${table_class} .dt-cell--sticky { background-color: var(--dt-cell-bg, var(--card-bg)); }
		/* sideways the rows move only under a script, from the strip below the table.
		   The table writes its own overflow onto the element, hence the shout */
		.${table_class} .dt-scrollable { overflow-x: hidden !important; }
		/* the track is drawn even when nothing is being dragged, so that there is
		   something to aim at; the thumb darkens under the cursor */
		.${table_class} .attendance-marks-scrollbar { position: relative; height: 14px;
			margin-top: 6px; border: 1px solid var(--border-color); border-radius: 7px;
			background-color: var(--control-bg, var(--fg-color)); }
		.${table_class} .attendance-marks-scrollbar .thumb { position: absolute; top: 1px;
			bottom: 1px; left: 0; min-width: 40px; border-radius: 6px; cursor: grab;
			background-color: var(--gray-500, #6B7280); opacity: 0.35;
			transition: opacity 120ms ease; }
		.${table_class} .attendance-marks-scrollbar:hover .thumb { opacity: 0.7; }
		.${table_class} .attendance-marks-scrollbar .thumb:active { opacity: 0.9;
			cursor: grabbing; }
	`;
	document.head.appendChild(style);
}

// the horizontal scrollbar of the table, taken out from under its last row. An overlay
// bar is drawn inside the box it belongs to, over whichever row happens to be at the
// bottom of it; the rows are left scrolling sideways under a script only, and the strip
// below the table is what the reader drags instead
function move_scrollbar() {
	const report = frappe.query_report.$report[0];
	const body = report.querySelector(".dt-scrollable");

	if (!body) return;

	let strip = report.querySelector(".attendance-marks-scrollbar");

	if (!strip) {
		strip = document.createElement("div");
		strip.className = "attendance-marks-scrollbar";
		strip.innerHTML = '<div class="thumb"></div>';
		report.appendChild(strip);
		// the box is a new element after every run, so it is looked up rather than held
		window.addEventListener("resize", () => {
			const current = report.querySelector(".dt-scrollable");
			if (current) paint_scrollbar(strip, current);
		});
	}

	// the table is built anew on every run, so the strip is pointed at the box of the day
	strip.onmousedown = (event) => grab_scrollbar(event, strip, body);

	if (!body.dataset.scrollsFromStrip) {
		body.dataset.scrollsFromStrip = "yes";
		body.addEventListener("wheel", (event) => {
			if (!event.deltaX) return;

			event.preventDefault();
			body.scrollLeft += event.deltaX;
			paint_scrollbar(strip, body);
		});
	}

	paint_scrollbar(strip, body);
}

// the thumb, sized and placed by how much of itself the table is showing
function paint_scrollbar(strip, body) {
	const shown = body.clientWidth / body.scrollWidth;

	strip.style.display = shown < 1 ? "" : "none";
	if (shown >= 1) return;

	const width = Math.max(shown * strip.clientWidth, 40);
	const scrolled = body.scrollLeft / (body.scrollWidth - body.clientWidth);

	strip.firstElementChild.style.width = `${width}px`;
	strip.firstElementChild.style.left = `${scrolled * (strip.clientWidth - width)}px`;
}

// dragging the thumb, or a click anywhere on the track to jump there
function grab_scrollbar(event, strip, body) {
	const thumb = strip.firstElementChild;
	const track = strip.getBoundingClientRect();
	const grip =
		event.target === thumb
			? event.clientX - thumb.getBoundingClientRect().left
			: thumb.offsetWidth / 2;

	const drag = (moved) => {
		const span = strip.clientWidth - thumb.offsetWidth;
		const offset = Math.min(Math.max(moved.clientX - track.left - grip, 0), span);

		body.scrollLeft = span ? (offset / span) * (body.scrollWidth - body.clientWidth) : 0;
		paint_scrollbar(strip, body);
	};

	const drop = () => {
		document.removeEventListener("mousemove", drag);
		document.removeEventListener("mouseup", drop);
	};

	event.preventDefault();
	drag(event);
	document.addEventListener("mousemove", drag);
	document.addEventListener("mouseup", drop);
}

// every other row a shade darker, in a grey without a tone of its own: the cursor is the
// only blue in the table, and a stripe that shares its tone reads as one. The rule is
// written per row rather than with
// :nth-child, because the table only keeps the visible rows in the DOM and their position
// among them shifts as it scrolls — the stripes would swap under the cursor
function stripe_rows() {
	const rows = (frappe.query_report.data || []).length;
	const even = Array.from({ length: rows }, (_, index) => index)
		.filter((index) => index % 2)
		.map((index) => `.attendance-marks .dt-row-${index} .dt-cell`);

	get_sheet_style("attendance-marks-stripes").textContent = even.length
		? `${even.join(",")} { background-color: var(--sheet-zebra); }`
		: "";
}

// the row and the column under the cursor. Nothing on the cells is touched: the rule
// names them by the classes the table already gave them, so following the cursor across
// a month of columns costs one line of CSS rewritten, not a class on every cell
function follow_cursor() {
	const report = frappe.query_report.$report[0];
	if (report.dataset.followsCursor) return;

	report.dataset.followsCursor = "yes";

	const paint = (event) => {
		const cell = event.target.closest(".dt-cell");
		const column = cell && cell.className.match(/dt-cell--col-(\d+)/);
		const row = cell && cell.closest(".dt-row").className.match(/dt-row-(\d+)/);
		const tint = "background-image: linear-gradient(var(--sheet-cross), var(--sheet-cross));";

		get_sheet_style("attendance-marks-cursor").textContent =
			column && row
				? `
				.attendance-marks .dt-row:not(.dt-row-filter) .dt-cell--col-${column[1]} { ${tint} }
				.attendance-marks .dt-row-${row[1]} .dt-cell { ${tint} }
			`
				: "";
	};

	report.addEventListener("mouseover", paint);
	report.addEventListener("mouseleave", paint);
}

function get_sheet_style(id) {
	let style = document.getElementById(id);

	if (!style) {
		style = document.createElement("style");
		style.id = id;
		document.head.appendChild(style);
	}

	return style;
}

// the totals read the way the sheet page prints them: two decimals at the most, and none
// at all where the number is whole
function format_total(value) {
	if (value === null || value === undefined || value === "") return "";

	return String(Math.round(flt(value) * 100) / 100);
}

// the day of a cell, the way the attendance sheet page draws it: the mark in the colour
// of its status, and under it the hours the day ran over or short, or the kind of leave.
// Both come from the server, so the report holds no table of its own to keep in step
function get_mark_html(value, mark) {
	const note = mark.note
		? `<span class="hours ${mark.note.kind}">${frappe.utils.escape_html(
				mark.note.text,
		  )}</span>`
		: "";
	const off = mark.off ? " off" : "";

	return `<span class="status${off}" style="color:${mark.color}">${value}</span>${note}`;
}

function set_reqd_filter(fieldname, is_reqd) {
	let filter = frappe.query_report.get_filter(fieldname);
	filter.df.reqd = is_reqd;
	filter.refresh();
}
function validate_date_range(report) {
	let start_date = frappe.query_report.get_filter_value("start_date");
	let end_date = frappe.query_report.get_filter_value("end_date");
	if (!(start_date && end_date)) return;

	let start = frappe.datetime.str_to_obj(start_date);
	let end = frappe.datetime.str_to_obj(end_date);
	let milli_seconds_in_a_day = 24 * 60 * 60 * 1000;
	let day_diff = Math.floor((end - start) / milli_seconds_in_a_day);
	if (day_diff > 90) {
		frappe.throw({
			message: __("Please set a date range less than 90 days."),
			title: __("Date Range Exceeded"),
		});
	}
	report.refresh();
}
