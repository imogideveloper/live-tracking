import frappe
from frappe import _
from frappe.utils import flt, get_url

STATUS_LABELS = {
	"Pending": _("Menunggu driver berangkat"),
	"Active": _("Sedang dalam perjalanan"),
	"Completed": _("Selesai"),
	"Cancelled": _("Dibatalkan"),
}

SESSION_FIELDS = [
	"name",
	"status",
	"driver_name",
	"driver_phone",
	"reference_doctype",
	"reference_name",
	"pickup_label",
	"pickup_latitude",
	"pickup_longitude",
	"dropoff_label",
	"dropoff_latitude",
	"dropoff_longitude",
	"current_latitude",
	"current_longitude",
	"current_heading",
	"last_ping_at",
	"started_at",
	"ended_at",
	"tracking_token",
]


@frappe.whitelist(allow_guest=True)
def get_tracking_info(tracking_token: str):
	if not tracking_token:
		frappe.throw(_("Link tracking tidak valid."), frappe.PermissionError)

	session = frappe.db.get_value(
		"Tracking Session",
		{"tracking_token": tracking_token},
		SESSION_FIELDS,
		as_dict=True,
	)
	if not session:
		frappe.throw(_("Link tracking tidak ditemukan atau sudah tidak berlaku."), frappe.PermissionError)

	return build_tracking_payload(session)


def build_tracking_payload(session):
	"""Shared by the guest-token endpoint above and the authenticated mobile
	endpoint (live_tracking.api.customer.get_trip_status) — same trip, two
	different ways of proving you're allowed to see it."""
	road_route = frappe.get_doc("Tracking Session", session.name).get_road_route()

	path = frappe.get_all(
		"Tracking Location Log",
		filters={"tracking_session": session.name},
		fields=["latitude", "longitude"],
		order_by="recorded_at asc",
		limit_page_length=500,
	)

	vehicle = None
	driver_vehicle = None
	if session.reference_doctype and session.reference_name and frappe.db.exists(
		session.reference_doctype, session.reference_name
	):
		ref = frappe.db.get_value(
			session.reference_doctype,
			session.reference_name,
			["nomor_polisi", "merk_kendaraan", "tipe_kendaraan", "kendaraan_towing"],
			as_dict=True,
		)
		if ref:
			# The customer's own vehicle being towed — not to be confused
			# with driver_vehicle below (the tow truck coming to get them).
			vehicle = {
				"nomor_polisi": ref.nomor_polisi,
				"merk": ref.merk_kendaraan,
				"tipe": ref.tipe_kendaraan,
			}
			if ref.kendaraan_towing:
				# merk/model dropped: not used by this business, and the
				# production "Unit Towing" table never got migrated to add
				# those columns, which crashed every driver/track load with
				# "Unknown column 'merk'" — not worth a migrate for data
				# nobody fills in.
				unit = frappe.db.get_value(
					"Unit Towing",
					ref.kendaraan_towing,
					["nomor_polisi", "jenis_kendaraan", "nomor_rangka"],
					as_dict=True,
				)
				if unit:
					driver_vehicle = {
						"nomor_polisi": unit.nomor_polisi,
						"jenis_kendaraan": unit.jenis_kendaraan,
						"nomor_rangka": unit.nomor_rangka,
					}

	return {
		"status": session.status,
		"status_label": str(STATUS_LABELS.get(session.status, session.status)),
		"driver_name": session.driver_name,
		"driver_phone": session.driver_phone,
		"vehicle": vehicle,
		"driver_vehicle": driver_vehicle,
		"pickup": {
			"label": session.pickup_label,
			"latitude": flt(session.pickup_latitude) or None,
			"longitude": flt(session.pickup_longitude) or None,
		},
		"dropoff": {
			"label": session.dropoff_label,
			"latitude": flt(session.dropoff_latitude) or None,
			"longitude": flt(session.dropoff_longitude) or None,
		},
		"current": {
			"latitude": session.current_latitude,
			"longitude": session.current_longitude,
			"heading": session.current_heading,
		},
		"path": [[p.latitude, p.longitude] for p in path],
		"planned_route": road_route,
		"last_ping_at": session.last_ping_at,
		"started_at": session.started_at,
		"ended_at": session.ended_at,
		# Same guest link the old web tracking page used — exposing it here
		# lets the app's own "Bagikan perjalanan" button reuse the existing
		# no-login /track page instead of building a second sharing mechanism.
		"share_url": get_url(f"/track?token={session.tracking_token}") if session.tracking_token else None,
	}
