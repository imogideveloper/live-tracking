(function () {
	"use strict";

	var PING_THROTTLE_MS = 8000;

	var app = document.getElementById("lt-driver-app");
	var sheet = document.getElementById("lt-sheet");
	var token = app ? app.getAttribute("data-token") : "";

	if (!token) {
		sheet.innerHTML = '<div class="lt-error">Link driver tidak valid.</div>';
		return;
	}

	var map = L.map("lt-map", { zoomControl: false, attributionControl: true }).setView([-6.2, 106.8], 13);
	// See the same fix in track.js — tile.openstreetmap.org blocks
	// distributed-app embedding, and CARTO's basemap now needs an API key
	// too. Esri's World Street Map tiles are free/keyless and meant for
	// this, confirmed with a real tile fetch before shipping this.
	L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", {
		maxZoom: 19,
		attribution: "Tiles &copy; Esri",
	}).addTo(map);

	var selfMarker = null;
	var watchId = null;
	var lastSentAt = 0;
	var wakeLock = null;

	function requestWakeLock() {
		if (!("wakeLock" in navigator)) return;
		navigator.wakeLock.request("screen").then(function (lock) {
			wakeLock = lock;
		}).catch(function () {});
	}

	function releaseWakeLock() {
		if (wakeLock) {
			wakeLock.release().catch(function () {});
			wakeLock = null;
		}
	}

	document.addEventListener("visibilitychange", function () {
		if (document.visibilityState === "visible" && watchId !== null) {
			requestWakeLock();
		}
	});

	function call(method, args) {
		return new Promise(function (resolve, reject) {
			frappe.call({
				type: "GET",
				method: "live_tracking.api.driver." + method,
				args: args,
				callback: function (r) { resolve(r.message); },
				error: function (r) { reject(r); },
			});
		});
	}

	function renderInfo(trip, extra) {
		var rows =
			'<div class="lt-driver-title">' + (trip.vehicle || "Perjalanan") + "</div>" +
			'<div class="lt-driver-sub">' + [trip.pickup_label, trip.dropoff_label].filter(Boolean).join(" &rarr; ") + "</div>";
		sheet.innerHTML = rows + (extra || "");
	}

	function onGeoSuccess(pos) {
		var lat = pos.coords.latitude;
		var lng = pos.coords.longitude;

		if (selfMarker) {
			selfMarker.setLatLng([lat, lng]);
		} else {
			selfMarker = L.marker([lat, lng]).addTo(map);
			map.setView([lat, lng], 16);
		}

		var now = Date.now();
		if (now - lastSentAt < PING_THROTTLE_MS) return;
		lastSentAt = now;

		var args = { driver_token: token, latitude: lat, longitude: lng };
		if (pos.coords.speed !== null && pos.coords.speed !== undefined) {
			args.speed = pos.coords.speed * 3.6;
		}
		if (pos.coords.heading !== null && pos.coords.heading !== undefined) {
			args.heading = pos.coords.heading;
		}
		if (pos.coords.accuracy !== null && pos.coords.accuracy !== undefined) {
			args.accuracy = pos.coords.accuracy;
		}

		call("ping_location", args).then(function () {
			var el = document.getElementById("lt-gps-status");
			if (!el) return;
			var acc = pos.coords.accuracy;
			var accText = acc ? " · akurasi ±" + Math.round(acc) + "m" : "";
			el.textContent = "Lokasi terkirim " + new Date().toLocaleTimeString() + accText;
			if (acc && acc > 100) {
				el.textContent += " (kurang akurat, pastikan izin lokasi diset ke 'Precise/Presisi' & mode High Accuracy aktif)";
				el.style.color = "#b45309";
			} else {
				el.style.color = "";
			}
		}).catch(function () {
			var el = document.getElementById("lt-gps-status");
			if (el) el.textContent = "Perjalanan sudah berakhir, berhenti mengirim lokasi.";
			stopWatching();
		});
	}

	function onGeoError(err) {
		var el = document.getElementById("lt-gps-status");
		if (el) el.textContent = "Gagal mengambil lokasi: " + err.message + ". Aktifkan izin lokasi di browser.";
	}

	function startWatching() {
		if (watchId !== null || !navigator.geolocation) return;
		watchId = navigator.geolocation.watchPosition(onGeoSuccess, onGeoError, {
			enableHighAccuracy: true,
			maximumAge: 5000,
			timeout: 15000,
		});
		requestWakeLock();
	}

	function stopWatching() {
		if (watchId !== null) {
			navigator.geolocation.clearWatch(watchId);
			watchId = null;
		}
		releaseWakeLock();
	}

	function renderActive(trip) {
		renderInfo(
			trip,
			'<div class="lt-pulse"><span class="lt-pulse-dot"></span> Mengirim lokasi live...</div>' +
			'<button class="lt-btn lt-btn-danger" id="lt-finish-btn">Selesai</button>' +
			'<div class="lt-gps-status" id="lt-gps-status">Menunggu sinyal GPS...</div>'
		);
		document.getElementById("lt-finish-btn").onclick = function (e) {
			e.target.disabled = true;
			call("finish_trip", { driver_token: token }).then(function () {
				stopWatching();
				renderDone(trip);
			});
		};
		startWatching();
	}

	function renderPending(trip) {
		renderInfo(
			trip,
			'<button class="lt-btn lt-btn-primary" id="lt-start-btn">Mulai Perjalanan</button>'
		);
		document.getElementById("lt-start-btn").onclick = function (e) {
			e.target.disabled = true;
			call("start_trip", { driver_token: token }).then(function () {
				renderActive(trip);
			});
		};
	}

	function renderDone(trip) {
		renderInfo(trip, '<div class="lt-error">Perjalanan sudah selesai. Terima kasih!</div>');
	}

	call("get_trip", { driver_token: token }).then(function (trip) {
		if (trip.status === "Active") {
			renderActive(trip);
		} else if (trip.status === "Completed" || trip.status === "Cancelled") {
			renderDone(trip);
		} else {
			renderPending(trip);
		}
	}).catch(function () {
		sheet.innerHTML = '<div class="lt-error">Link driver tidak ditemukan atau sudah tidak berlaku.</div>';
	});
})();
