// Plays a sound + shows an alert in Frappe Desk whenever a new towing order
// comes in from the mobile app (live_tracking.api.customer.create_towing_request
// publishes the "towing_new_order" realtime event right after the Sales
// Order/Delivery Order Towing get auto-created).
//
// Loaded on every Desk page (app_include_js in hooks.py) since a dispatcher
// needs to hear this regardless of which screen they're currently on.

(function () {
	function canHearTowingAlerts() {
		if (!frappe.session || frappe.session.user === "Guest") return false;
		var roles = frappe.user_roles || [];
		return (
			roles.indexOf("System Manager") !== -1 ||
			roles.indexOf("Admin Towing") !== -1 ||
			roles.indexOf("Sales Manager") !== -1
		);
	}

	function playAlertSound() {
		try {
			var audio = new Audio("/assets/frappe/sounds/alert.mp3");
			audio.play();
		} catch (e) {
			console.log("Tidak bisa memutar suara notifikasi towing", e);
		}
	}

	function register() {
		if (!canHearTowingAlerts()) return;

		frappe.realtime.on("towing_new_order", function (data) {
			playAlertSound();

			frappe.show_alert(
				{
					message: __("🚨 Order Towing Baru dari {0} — {1} → {2}", [
						data.customer_name || "Customer",
						data.pickup_label || "?",
						data.dropoff_label || "?",
					]),
					indicator: "orange",
				},
				10
			);
		});
	}

	// frappe.ready isn't guaranteed to exist yet the instant this file's own
	// <script> tag runs on bench's dev server (unbundled assets load in a
	// different order than a built production bundle, where frappe-core
	// always loads first) — calling it straight away threw
	// "frappe.ready is not a function" here, which silently aborted every
	// OTHER doctype_js script queued after this one on the same page too
	// (e.g. the Live Tracking icon never appeared on Delivery Order Towing).
	// Wait for it instead of assuming it's there.
	function when_frappe_ready(fn, attempts_left) {
		if (window.frappe && typeof frappe.ready === "function") {
			frappe.ready(fn);
			return;
		}
		if (attempts_left <= 0) return;
		setTimeout(function () {
			when_frappe_ready(fn, attempts_left - 1);
		}, 100);
	}

	when_frappe_ready(register, 50);
})();
