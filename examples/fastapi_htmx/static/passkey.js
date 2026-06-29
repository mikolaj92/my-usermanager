(function () {
  "use strict";

  function setStatus(message) {
    var region = document.querySelector("[data-passkey-status]");
    if (region) {
      region.textContent = message;
    }
  }

  function describeSupport() {
    if (!window.PublicKeyCredential || !navigator.credentials) {
      return "Passkey APIs are not available in this browser context.";
    }
    return "Passkey APIs are available. The demo host has not requested credentials.";
  }

  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-passkey-demo]");
    if (!trigger) {
      return;
    }
    setStatus(describeSupport());
  });
})();
