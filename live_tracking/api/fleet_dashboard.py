"""Admin-facing fleet snapshot — every active Driver's current state (on the
road / idle / offline). Powers the "Fleet Monitor" desk page; System Users
only (drivers/customers are Website Users and can't reach Desk pages at all,
so no extra role-gating is needed beyond the default @frappe.whitelist()).
"""

import frappe

from live_tracking.api.driver_mobile import ACTIVE_DO_STATUSES

# Rounding to 4 decimal places (~11m) means small GPS jitter while a driver
# is stationary still hits the same cache key, instead of re-geocoding on
# every single 8s poll — keeps us well under Nominatim's ~1 req/sec policy
# even with several admins watching the dashboard at once.
ADDRESS_CACHE_TTL = 300


def _real_position(lat, lng):
	"""None unless both values are set AND not (0, 0) — Frappe Float fields
	default to 0 (not null) until a driver's phone ever actually sends a
	GPS ping, and (0, 0) ("Null Island", off the African coast) is never a
	real position for this fleet. Returning it as-is let the mobile app's
	map zoom out to show the whole world with a driver pinned in the
	Atlantic — same bug, fixed at the source instead of just client-side."""
	if not lat or not lng:
		return None, None
	if abs(lat) < 0.0001 and abs(lng) < 0.0001:
		return None, None
	return lat, lng


def _get_address_label(latitude, longitude):
	if not latitude or not longitude:
		return None

	cache_key = f"fleet_dashboard_addr:{round(float(latitude), 4)}:{round(float(longitude), 4)}"
	cached = frappe.cache().get_value(cache_key)
	if cached is not None:
		return cached or None

	from live_tracking.api.geo import reverse_geocode

	try:
		label = reverse_geocode(latitude, longitude).get("label")
	except Exception:
		label = None

	frappe.cache().set_value(cache_key, label or "", expires_in_sec=ADDRESS_CACHE_TTL)
	return label


@frappe.whitelist()
def get_fleet_overview():
	drivers = frappe.get_all(
		"Driver",
		filters={"status": "Active"},
		fields=[
			"name",
			"full_name",
			"cell_number",
			"custom_is_online as is_online",
			"custom_current_latitude as latitude",
			"custom_current_longitude as longitude",
			"custom_last_location_at as last_location_at",
		],
		order_by="full_name asc",
	)

	active_dos = frappe.get_all(
		"Delivery Order Towing",
		filters={"status": ["in", ACTIVE_DO_STATUSES], "docstatus": ["<", 2]},
		fields=["name", "driver", "status", "customer_name", "lokasi_pickup", "lokasi_tujuan"],
	)
	active_by_driver = {d.driver: d for d in active_dos}

	session_by_do = {}
	if active_dos:
		sessions = frappe.get_all(
			"Tracking Session",
			filters={"reference_name": ["in", [d.name for d in active_dos]]},
			fields=[
				"reference_name",
				"current_latitude",
				"current_longitude",
				"current_heading",
				"current_speed_kmh",
				"last_ping_at",
				"started_at",
			],
		)
		session_by_do = {s.reference_name: s for s in sessions}

	result = []
	for d in drivers:
		do = active_by_driver.get(d.name)
		if do:
			session = session_by_do.get(do.name)
			lat, lng = _real_position(
				(session.current_latitude if session else None) or d.latitude,
				(session.current_longitude if session else None) or d.longitude,
			)
			result.append(
				{
					"name": d.name,
					"full_name": d.full_name,
					"cell_number": d.cell_number,
					"status": "on_trip",
					"latitude": lat,
					"longitude": lng,
					"address": _get_address_label(lat, lng),
					"heading": session.current_heading if session else None,
					"speed_kmh": session.current_speed_kmh if session else None,
					"last_update": session.last_ping_at if session else d.last_location_at,
					"trip": {
						"delivery_order_towing": do.name,
						"status": do.status,
						"customer_name": do.customer_name,
						"lokasi_pickup": do.lokasi_pickup,
						"lokasi_tujuan": do.lokasi_tujuan,
						"started_at": session.started_at if session else None,
					},
				}
			)
		else:
			# Skip geocoding offline drivers — they're not plotted on the map
			# either, and there's no need to burn Nominatim calls on a
			# position nobody's currently acting on.
			is_online = bool(d.is_online)
			lat, lng = _real_position(d.latitude, d.longitude)
			result.append(
				{
					"name": d.name,
					"full_name": d.full_name,
					"cell_number": d.cell_number,
					"status": "idle" if is_online else "offline",
					"latitude": lat,
					"longitude": lng,
					"address": _get_address_label(lat, lng) if is_online else None,
					"heading": None,
					"speed_kmh": None,
					"last_update": d.last_location_at,
					"trip": None,
				}
			)

	return result
