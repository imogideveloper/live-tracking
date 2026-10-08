app_name = "live_tracking"
app_title = "Live Tracking"
app_publisher = "Imogi"
app_description = "Gojek-style live GPS tracking for towing delivery orders"
app_email = "imogi.indonesia@gmail.com"
app_license = "mit"

# Apps
# ------------------

required_apps = ["imogi_finance"]

# Fixtures
# ------------------

fixtures = [
	{"doctype": "Custom Field", "filters": [["name", "in", [
		"Driver-custom_user",
		"Driver-custom_is_online",
		"Driver-custom_current_latitude",
		"Driver-custom_current_longitude",
		"Driver-custom_last_location_at",
		"Driver-custom_unit_towing",
		"Customer-custom_user",
	]]]},
	{"doctype": "Role", "filters": [["name", "in", ["Towing Customer", "Towing Driver"]]]},
	{"doctype": "Custom DocPerm", "filters": [
		["parent", "=", "Delivery Order Towing"], ["role", "=", "Towing Driver"]
	]},
	{"doctype": "Client Script", "filters": [
		["name", "=", "Delivery Order Towing-Form-Live-Tracking"]
	]},
]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "live_tracking",
# 		"logo": "/assets/live_tracking/logo.png",
# 		"title": "Live Tracking",
# 		"route": "/live_tracking",
# 		"has_permission": "live_tracking.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/live_tracking/css/live_tracking.css"
app_include_js = "/assets/live_tracking/js/towing_order_alert.js"

# include js, css files in header of web template
# web_include_css = "/assets/live_tracking/css/live_tracking.css"
# web_include_js = "/assets/live_tracking/js/live_tracking.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "live_tracking/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# NOTE: "Delivery Order Towing" is a Custom DocType — Frappe's
# add_code() skips doctype_js entirely for custom doctypes (see
# frappe/desk/form/meta.py: `if self.custom: return`), so a hook here
# would silently never load. The live-tracking icon/dialog on that form
# is delivered via a "Client Script" fixture instead (see
# fixtures/client_script.json) — the mechanism that actually works for
# custom doctypes.
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "live_tracking/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "live_tracking.utils.jinja_methods",
# 	"filters": "live_tracking.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "live_tracking.install.before_install"
# after_install = "live_tracking.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "live_tracking.uninstall.before_uninstall"
# after_uninstall = "live_tracking.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "live_tracking.utils.before_app_install"
# after_app_install = "live_tracking.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "live_tracking.utils.before_app_uninstall"
# after_app_uninstall = "live_tracking.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "live_tracking.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Delivery Order Towing": {
		# A submitted doc's later saves fire "on_update_after_submit"
		# INSTEAD OF "on_update" (see frappe/model/document.py
		# run_post_save_methods) — and every workflow transition past the
		# initial Draft->Assigned one happens on an already-submitted DO, so
		# both events need the same handlers or status changes past
		# "Assigned" (Pick Up, Delivered, Done, ...) never sync anywhere.
		# "on_cancel" covers an actual docstatus 1->2 cancellation too.
		"on_update": [
			"live_tracking.integrations.delivery_order_towing.sync_tracking_session",
			"live_tracking.integrations.delivery_order_towing.sync_towing_request_status",
		],
		"on_update_after_submit": [
			"live_tracking.integrations.delivery_order_towing.sync_tracking_session",
			"live_tracking.integrations.delivery_order_towing.sync_towing_request_status",
		],
		"on_cancel": [
			"live_tracking.integrations.delivery_order_towing.sync_tracking_session",
			"live_tracking.integrations.delivery_order_towing.sync_towing_request_status",
		],
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		"*/1 * * * *": [
			"live_tracking.api.matching.cleanup_stale_offers",
		],
	},
}

# Testing
# -------

# before_tests = "live_tracking.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "live_tracking.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "live_tracking.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["live_tracking.utils.before_request"]
after_request = ["live_tracking.utils.allow_geolocation_for_tracking_pages"]

# Job Events
# ----------
# before_job = ["live_tracking.utils.before_job"]
# after_job = ["live_tracking.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"live_tracking.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

