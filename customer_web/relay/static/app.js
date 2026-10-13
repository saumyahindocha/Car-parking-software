(function () {
  "use strict";
  // show the "Open UPI app" button only on phones (UPI intent links do nothing on desktops)
  var mobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent || "");
  document.querySelectorAll(".upi-app").forEach(function (a) { if (!mobile) a.style.display = "none"; });

  // upper-case plate inputs as the customer types
  document.querySelectorAll("input[name=plate]").forEach(function (el) {
    el.addEventListener("input", function () {
      var p = el.selectionStart; el.value = el.value.toUpperCase(); try { el.setSelectionRange(p, p); } catch (e) {}
    });
  });

  // payment status polling (QR page: reload when paid; done page: show the receipt link when synced)
  var box = document.querySelector("[data-status-url]");
  if (!box || !window.fetch) return;
  var url = box.getAttribute("data-status-url"), tries = 0, onDone = box.id === "done";
  function poll() {
    tries++;
    fetch(url, { credentials: "same-origin", cache: "no-store" }).then(function (r) { return r.json(); }).then(function (d) {
      if (onDone) {
        if (d.receipt_url) { window.location.href = d.receipt_url; return; }
      } else if (d.status !== "PENDING") { window.location.reload(); return; }
      schedule();
    }).catch(schedule);
  }
  function schedule() { if (tries < 200) setTimeout(poll, tries < 20 ? 3000 : 6000); }
  schedule();
})();
