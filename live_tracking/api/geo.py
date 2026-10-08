"""Reverse geocoding for the mobile apps — drop a pin, get a human-readable
address back, instead of asking the user to type one.

Same Nominatim public service already used for forward-geocoding on
Tracking Session (apps/live_tracking/live_tracking/live_tracking/doctype/
tracking_session/tracking_session.py) — routed through our own backend
(rather than called directly from the app) so the required User-Agent
header and ~1 req/sec usage policy are honored consistently in one place,
and so the mobile client never needs to deal with Nominatim's CORS/rate
-limit quirks directly.
"""

import frappe
from frappe import _

NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "live_tracking-frappe-app/1.0"}

# Same public OSRM demo server Tracking Session already uses for the
# post-match live route (tracking_session.py::_fetch_road_route) — routes by
# road-network distance/speed limits only, not real-time traffic, since that
# needs a paid provider (Google/Mapbox traffic-aware directions).
OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"


@frappe.whitelist()
def reverse_geocode(latitude: float, longitude: float):
	import requests

	try:
		resp = requests.get(
			NOMINATIM_REVERSE_URL,
			params={"lat": latitude, "lon": longitude, "format": "json", "zoom": 18},
			headers=NOMINATIM_HEADERS,
			timeout=8,
		)
		data = resp.json()
	except Exception:
		frappe.log_error(
			f"Gagal reverse geocode ({latitude}, {longitude})", "Live Tracking Reverse Geocode Error"
		)
		return {"label": None}

	if not data or "display_name" not in data:
		return {"label": None}

	return {"label": data["display_name"]}


@frappe.whitelist()
def search_place(query: str):
	"""Forward-geocode a free-text query into a short list of matching places
	— lets the customer app search a destination by name instead of only
	dropping a pin, same idea as reverse_geocode but the other direction."""
	import requests

	query = (query or "").strip()
	if len(query) < 3:
		return []

	try:
		resp = requests.get(
			NOMINATIM_SEARCH_URL,
			params={"q": query, "format": "json", "limit": 5, "countrycodes": "id"},
			headers=NOMINATIM_HEADERS,
			timeout=8,
		)
		results = resp.json()
	except Exception:
		frappe.log_error(f"Gagal search place '{query}'", "Live Tracking Search Place Error")
		return []

	return [
		{"label": r["display_name"], "latitude": float(r["lat"]), "longitude": float(r["lon"])}
		for r in results
	]


@frappe.whitelist()
def get_route(pickup_latitude: float, pickup_longitude: float, dropoff_latitude: float, dropoff_longitude: float):
	"""Road-network route preview for the booking screen (before a driver is
	matched and a Tracking Session exists) — a plain list of [lat, lng]
	points, same shape as Tracking Session.get_road_route()."""
	import requests

	url = OSRM_URL.format(
		lon1=pickup_longitude, lat1=pickup_latitude, lon2=dropoff_longitude, lat2=dropoff_latitude
	)
	try:
		resp = requests.get(url, params={"overview": "full", "geometries": "geojson"}, timeout=8)
		data = resp.json()
		coords = data["routes"][0]["geometry"]["coordinates"]
		return [[lat, lng] for lng, lat in coords]
	except Exception:
		frappe.log_error(
			f"Gagal ambil rute OSRM ({pickup_latitude},{pickup_longitude}) -> "
			f"({dropoff_latitude},{dropoff_longitude})",
			"Live Tracking OSRM Error",
		)
		return []
