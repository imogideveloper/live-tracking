"""Shared road-routing helper — OSRM calls used by both the static
pickup-to-dropoff planned route (tracking_session.py) and the live
"driver's current position to whichever leg they're on" route added for
the driver app and the owner's fleet map.
"""

import frappe

# Same public OSRM demo server tracking_session.py already uses for the
# planned route — free, no key, fine for this volume.
OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"


def real_position(lat, lng):
	"""None unless both values are set AND not (0, 0) — Frappe Float fields
	default to 0 (not null), and (0, 0) is never a real position for this
	(Indonesia-only) fleet. Same rule as fleet_dashboard._real_position,
	duplicated here rather than imported so routing.py has no dependency
	on a module that isn't always loaded for every caller."""
	if not lat or not lng:
		return None, None
	try:
		lat, lng = float(lat), float(lng)
	except (TypeError, ValueError):
		return None, None
	if abs(lat) < 0.0001 and abs(lng) < 0.0001:
		return None, None
	return lat, lng


def fetch_route(from_lat, from_lng, to_lat, to_lng, cache_seconds=20):
	"""Road-following route from one point to another:
	`{"points": [[lat, lng], ...], "distance_m": float|None, "duration_s": float|None}`.
	Empty/None fields mean no coordinates to route between, or the OSRM
	call itself failed.

	Short-TTL cached because the "from" point here is a moving driver — a
	fresh call on literally every GPS ping (as often as every 15-20s) would
	hammer the public OSRM demo server for no visible benefit. Rounding
	`from_lat`/`from_lng` to 3 decimals (~111m buckets) meant a driver who
	hadn't crossed that whole bucket yet — easy on foot, or even driving
	for a short test — kept getting back the SAME route computed from
	wherever they started, even though the live marker (now a separate,
	continuously-updating GPS stream on the client) had visibly moved on.
	That read as "the route doesn't go where I'm actually heading" or
	loops back to the start. 4 decimals (~11m) plus a TTL matching the
	ping cadence keeps the route meaningfully fresher without still
	calling OSRM on every single frame.
	"""
	empty = {"points": [], "distance_m": None, "duration_s": None}

	from_lat, from_lng = real_position(from_lat, from_lng)
	to_lat, to_lng = real_position(to_lat, to_lng)
	if not (from_lat and to_lat):
		return empty

	cache_key = (
		f"lt_leg_route:{round(from_lat, 4)}:{round(from_lng, 4)}:"
		f"{round(to_lat, 4)}:{round(to_lng, 4)}"
	)
	cached = frappe.cache().get_value(cache_key)
	if cached is not None:
		return cached

	import requests

	result = dict(empty)
	try:
		url = OSRM_URL.format(lon1=from_lng, lat1=from_lat, lon2=to_lng, lat2=to_lat)
		resp = requests.get(url, params={"overview": "full", "geometries": "geojson"}, timeout=8)
		data = resp.json()
		route = data["routes"][0]
		coords = route["geometry"]["coordinates"]
		result = {
			"points": [[lat, lng] for lng, lat in coords],
			"distance_m": route.get("distance"),
			"duration_s": route.get("duration"),
		}
	except Exception:
		frappe.log_error("Gagal ambil rute OSRM (live leg)", "Live Tracking OSRM Error")

	frappe.cache().set_value(cache_key, result, expires_in_sec=cache_seconds)
	return result


def leg_target_for_status(status, session_row):
	"""Which point the driver is currently heading toward: the pickup spot
	until they've confirmed pickup, the dropoff spot after — matches the
	Assigned -> Pick Up -> Delivered workflow on Delivery Order Towing."""
	if status == "Assigned":
		return "pickup", session_row.get("pickup_label"), session_row.get("pickup_latitude"), session_row.get("pickup_longitude")
	return "dropoff", session_row.get("dropoff_label"), session_row.get("dropoff_latitude"), session_row.get("dropoff_longitude")
