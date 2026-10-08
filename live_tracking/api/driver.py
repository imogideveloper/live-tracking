import frappe
from frappe import _
from frappe.utils import flt, now_datetime

SESSION_FIELDS = [
	"name",
	"status",
	"driver_name",
	"reference_doctype",
	"reference_name",
	"pickup_label",
	"dropoff_label",
	"started_at",
	"ended_at",
]


def _get_session(driver_token: str):
	if not driver_token:
		frappe.throw(_("Link driver tidak valid."), frappe.PermissionError)

	session = frappe.db.get_value(
		"Tracking Session",
		{"driver_token": driver_token},
		SESSION_FIELDS,
		as_dict=True,
	)
	if not session:
		frappe.throw(_("Link driver tidak ditemukan atau sudah tidak berlaku."), frappe.PermissionError)
	return session


@frappe.whitelist(allow_guest=True)
def get_trip(driver_token: str):
	session = _get_session(driver_token)
	reference_label = None
	if session.reference_doctype and session.reference_name:
		reference_label = frappe.db.get_value(
			session.reference_doctype, session.reference_name, "nomor_polisi"
		) or session.reference_name

	return {
		"status": session.status,
		"driver_name": session.driver_name,
		"vehicle": reference_label,
		"pickup_label": session.pickup_label,
		"dropoff_label": session.dropoff_label,
		"started_at": session.started_at,
		"ended_at": session.ended_at,
	}


@frappe.whitelist(allow_guest=True)
def start_trip(driver_token: str):
	session = _get_session(driver_token)
	if session.status == "Completed" or session.status == "Cancelled":
		frappe.throw(_("Perjalanan ini sudah berakhir."))

	frappe.db.set_value(
		"Tracking Session",
		session.name,
		{
			"status": "Active",
			"started_at": session.started_at or now_datetime(),
		},
	)
	frappe.db.commit()
	return {"status": "Active"}


def record_ping(
	session_name: str,
	session_status: str,
	session_started_at,
	latitude: float,
	longitude: float,
	speed: float = None,
	heading: float = None,
	accuracy: float = None,
):
	"""Shared by the guest-token `ping_location` below and the authenticated
	mobile equivalent (live_tracking.api.driver_mobile.ping_location)."""
	if session_status not in ("Pending", "Active"):
		frappe.throw(_("Perjalanan ini sudah berakhir, lokasi tidak lagi diterima."))

	latitude = flt(latitude, 6)
	longitude = flt(longitude, 6)
	now = now_datetime()

	log = frappe.new_doc("Tracking Location Log")
	log.tracking_session = session_name
	log.latitude = latitude
	log.longitude = longitude
	log.speed_kmh = flt(speed) if speed is not None else None
	log.heading = flt(heading) if heading is not None else None
	log.accuracy_m = flt(accuracy) if accuracy is not None else None
	log.recorded_at = now
	log.insert(ignore_permissions=True)

	update = {
		"current_latitude": latitude,
		"current_longitude": longitude,
		"last_ping_at": now,
	}
	if speed is not None:
		update["current_speed_kmh"] = flt(speed)
	if heading is not None:
		update["current_heading"] = flt(heading)
	if session_status == "Pending":
		update["status"] = "Active"
		if not session_started_at:
			update["started_at"] = now

	frappe.db.set_value("Tracking Session", session_name, update)
	frappe.db.commit()

	frappe.publish_realtime(
		"live_tracking_update",
		{
			"tracking_session": session_name,
			"latitude": latitude,
			"longitude": longitude,
			"speed_kmh": update.get("current_speed_kmh"),
			"heading": update.get("current_heading"),
			"last_ping_at": now,
			"status": update.get("status", session_status),
		},
		doctype="Tracking Session",
		docname=session_name,
	)

	return {"success": True}


@frappe.whitelist(allow_guest=True)
def ping_location(
	driver_token: str,
	latitude: float,
	longitude: float,
	speed: float = None,
	heading: float = None,
	accuracy: float = None,
):
	session = _get_session(driver_token)
	return record_ping(
		session.name, session.status, session.started_at, latitude, longitude, speed, heading, accuracy
	)


@frappe.whitelist(allow_guest=True)
def finish_trip(driver_token: str):
	session = _get_session(driver_token)
	frappe.db.set_value(
		"Tracking Session",
		session.name,
		{
			"status": "Completed",
			"ended_at": session.ended_at or now_datetime(),
		},
	)
	frappe.db.commit()

	frappe.publish_realtime(
		"live_tracking_update",
		{"tracking_session": session.name, "status": "Completed"},
		doctype="Tracking Session",
		docname=session.name,
	)
	return {"status": "Completed"}
