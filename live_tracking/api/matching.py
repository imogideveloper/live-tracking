"""Auto-matching engine: broadcasts a Towing Request to nearby online drivers.

Not whitelisted on purpose — this module is called internally (via
frappe.enqueue and the scheduler), never directly from the mobile app.
"""

import math
import time

import frappe
from frappe.utils import add_to_date, now_datetime

from live_tracking.api import push

# How long a round of offers stays open before we expand the radius / give up.
OFFER_TTL_SECONDS = 20
# Search radius bands, expanded round over round (km).
RADIUS_BANDS_KM = [5, 10, 20]
# How many idle drivers get offered a job per round.
DRIVERS_PER_ROUND = 3
# A driver's standing location older than this is considered stale (minutes).
LOCATION_FRESH_MINUTES = 10


def haversine_km(lat1, lng1, lat2, lng2):
	radius = 6371.0
	phi1, phi2 = math.radians(lat1), math.radians(lat2)
	d_phi = math.radians(lat2 - lat1)
	d_lambda = math.radians(lng2 - lng1)
	a = (
		math.sin(d_phi / 2) ** 2
		+ math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
	)
	return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _is_driver_busy(driver: str) -> bool:
	return bool(
		frappe.db.exists(
			"Delivery Order Towing",
			{
				"driver": driver,
				"status": ["in", ["Assigned", "Pick Up", "Delivered"]],
				"docstatus": ["<", 2],
			},
		)
	)


def find_nearby_drivers(lat: float, lng: float, radius_km: float, exclude: list[str] | None = None):
	exclude = set(exclude or [])
	stale_before = add_to_date(now_datetime(), minutes=-LOCATION_FRESH_MINUTES)

	rows = frappe.get_all(
		"Driver",
		filters={
			"status": "Active",
			"custom_is_online": 1,
			"custom_current_latitude": ["is", "set"],
			"custom_current_longitude": ["is", "set"],
			"custom_last_location_at": [">=", stale_before],
		},
		fields=["name", "custom_current_latitude", "custom_current_longitude"],
	)

	candidates = []
	for row in rows:
		if row.name in exclude or _is_driver_busy(row.name):
			continue
		distance = haversine_km(lat, lng, row.custom_current_latitude, row.custom_current_longitude)
		if distance <= radius_km:
			candidates.append({"driver": row.name, "distance_km": round(distance, 2)})

	candidates.sort(key=lambda c: c["distance_km"])
	return candidates


def broadcast_job(request_name: str):
	"""Runs inside a background worker (enqueued right after a Towing Request is created)."""
	request = frappe.get_doc("Towing Request", request_name)
	if request.status != "Searching":
		return

	offered_drivers: set[str] = set()

	for round_no, radius_km in enumerate(RADIUS_BANDS_KM, start=1):
		request.reload()
		if request.status != "Searching":
			return

		candidates = find_nearby_drivers(
			request.pickup_latitude, request.pickup_longitude, radius_km, exclude=list(offered_drivers)
		)[:DRIVERS_PER_ROUND]

		if not candidates:
			continue

		offers = []
		for candidate in candidates:
			offer = frappe.get_doc(
				{
					"doctype": "Towing Job Offer",
					"towing_request": request.name,
					"driver": candidate["driver"],
					"status": "Pending",
					"round_no": round_no,
					"distance_km": candidate["distance_km"],
					"offered_at": now_datetime(),
				}
			).insert(ignore_permissions=True)
			offers.append(offer.name)
			offered_drivers.add(candidate["driver"])

			driver_user = frappe.db.get_value("Driver", candidate["driver"], "custom_user")
			if driver_user:
				push.send_push(
					driver_user,
					"Order Towing Baru",
					f"Jemput di {request.pickup_label} ({candidate['distance_km']} km)",
					{"type": "job_offer", "offer": offer.name, "towing_request": request.name},
				)

		frappe.db.set_value("Towing Request", request.name, "status", "Offered")
		frappe.db.commit()

		deadline = time.time() + OFFER_TTL_SECONDS
		while time.time() < deadline:
			time.sleep(2)
			request.reload()
			if request.status in ("Assigned", "Cancelled"):
				return

		# Nobody in this round accepted in time — expire only offers still pending
		# (an offer a driver already rejected keeps its "Rejected" status).
		for offer_name in offers:
			if frappe.db.get_value("Towing Job Offer", offer_name, "status") == "Pending":
				frappe.db.set_value(
					"Towing Job Offer", offer_name, "status", "Expired", update_modified=False
				)
		frappe.db.set_value("Towing Request", request.name, "status", "Searching")
		frappe.db.commit()

	request.reload()
	if request.status == "Searching":
		frappe.db.set_value("Towing Request", request.name, "status", "No Driver Found")
		frappe.db.commit()
		push.send_push(
			request.user,
			"Tidak Ada Driver Tersedia",
			"Maaf, belum ada driver towing yang tersedia di sekitar lokasi Anda. Silakan coba lagi.",
			{"type": "no_driver", "towing_request": request.name},
		)


def cleanup_stale_offers():
	"""Scheduled safety net (every minute) in case a broadcast worker died mid-flight."""
	stale_offer_before = add_to_date(now_datetime(), minutes=-2)
	frappe.db.sql(
		"""
		UPDATE `tabTowing Job Offer`
		SET status = 'Expired'
		WHERE status = 'Pending' AND offered_at < %s
		""",
		(stale_offer_before,),
	)

	stale_request_before = add_to_date(now_datetime(), minutes=-6)
	stuck_requests = frappe.get_all(
		"Towing Request",
		filters={"status": ["in", ["Searching", "Offered"]], "requested_at": ["<", stale_request_before]},
		pluck="name",
	)
	for name in stuck_requests:
		frappe.db.set_value("Towing Request", name, "status", "No Driver Found")

	if stuck_requests:
		frappe.db.commit()
