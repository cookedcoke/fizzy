/* Security keys (WebAuthn): adding one in Settings, and signing in with one. The server sends the
   options as JSON with base64url bytes; the browser API wants ArrayBuffers, and back again. */
(function () {
  if (!window.PublicKeyCredential || !navigator.credentials) return;
  function buf(s) {
    s = s.replace(/-/g, "+").replace(/_/g, "/"); while (s.length % 4) s += "=";
    return Uint8Array.from(atob(s), function (c) { return c.charCodeAt(0); }).buffer;
  }
  function b64(b) {
    var s = "", a = new Uint8Array(b); for (var i = 0; i < a.length; i++) s += String.fromCharCode(a[i]);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function ids(list) { return (list || []).map(function (c) { return Object.assign({}, c, { id: buf(c.id) }); }); }
  function pack(cred) {
    var r = cred.response, out = { id: cred.id, rawId: b64(cred.rawId), type: cred.type, clientExtensionResults: {},
      response: { clientDataJSON: b64(r.clientDataJSON) } };
    if (r.attestationObject) {
      out.response.attestationObject = b64(r.attestationObject);
      if (r.getTransports) out.response.transports = r.getTransports();
    } else {
      out.response.authenticatorData = b64(r.authenticatorData); out.response.signature = b64(r.signature);
      out.response.userHandle = r.userHandle ? b64(r.userHandle) : null;
    }
    return JSON.stringify(out);
  }
  function sorry(e) {
    return e && e.name === "NotAllowedError" ? "Cancelled, or the key took too long. Try again."
      : e && e.name === "InvalidStateError" ? "That key is already added." : "That didn't work. Try again.";
  }

  var add = document.querySelector("[data-passkey-add]");
  if (add) {
    var o = JSON.parse(add.querySelector("script").textContent), form = add.querySelector("form"), msg = add.querySelector("[data-passkey-msg]");
    o.challenge = buf(o.challenge); o.user.id = buf(o.user.id); o.excludeCredentials = ids(o.excludeCredentials);
    add.querySelector("button").addEventListener("click", function () {
      msg.textContent = "Touch the key...";
      navigator.credentials.create({ publicKey: o }).then(function (c) {
        form.credential.value = pack(c); form.submit();
      }, function (e) { msg.textContent = sorry(e); });
    });
  }

  var go = document.querySelector("[data-passkey-login]");
  if (go) {
    var pf = document.getElementById("passkey-form"), err = function (t) {
      var box = document.querySelector(".login .err");
      if (!box) { box = document.createElement("div"); box.className = "err"; document.querySelector(".login form").before(box); }
      box.textContent = t;
    };
    go.hidden = false;
    go.addEventListener("click", function () {
      fetch("/login/passkey/options", { method: "POST", credentials: "same-origin" })
        .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || "That didn't work."); return j; }); })
        .then(function (o) {
          o.challenge = buf(o.challenge); o.allowCredentials = ids(o.allowCredentials);
          return navigator.credentials.get({ publicKey: o });
        })
        .then(function (c) {
          pf.who.value = document.getElementById("who").value; pf.credential.value = pack(c); pf.submit();
        }, function (e) { err(e && e.name === "Error" ? e.message : sorry(e)); });  // "Error" is ours, from the server
    });
  }
})();
