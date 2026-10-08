import frappe

# imogi_pos sets a site-wide "Permissions-Policy: geolocation=()" header via its own
# after_request hook, which blocks the Geolocation API on every page — including ours.
# Since live_tracking's hooks run after imogi_pos's (see sites/apps.txt order), we can
# relax the policy again for just our own tracking pages without touching imogi_pos.
TRACKING_PATHS = ("/track", "/driver")


def allow_geolocation_for_tracking_pages(response, request=None):
	path = (frappe.request.path if frappe.request else "").rstrip("/")
	if path in TRACKING_PATHS:
		response.headers["Permissions-Policy"] = "geolocation=(self)"
	return response
