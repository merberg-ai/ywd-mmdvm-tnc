"use strict";
(() => {
  const overlay = document.getElementById("auth-overlay");
  const form = document.getElementById("auth-form");
  const status = document.getElementById("auth-status");
  const password = document.getElementById("password");
  const button = document.getElementById("sign-in");
  const logout = document.getElementById("logout");
  const state = document.getElementById("session-state");
  const log = document.getElementById("log");
  let csrf = "";

  function message(text) {
    const line = document.createElement("div");
    const prefix = document.createElement("span");
    prefix.className = "ts";
    prefix.textContent = new Date().toLocaleTimeString();
    line.append(prefix, document.createTextNode(text));
    log.appendChild(line);
  }
  async function request(url, options = {}) {
    const result = await fetch(url, { credentials: "same-origin", cache: "no-store", ...options });
    return { ok: result.ok, code: result.status, body: await result.json() };
  }
  async function refresh() {
    try {
      const result = await request("/api/session");
      if (result.ok && result.body.authenticated) {
        csrf = result.body.csrf;
        overlay.hidden = true;
        logout.hidden = false;
        state.textContent = "AUTHENTICATED / FM0";
        message("Authenticated browser session established; hardware access remains disabled");
      } else {
        csrf = "";
        overlay.hidden = false;
        logout.hidden = true;
        state.textContent = "LOCKED";
      }
    } catch (_) {
      overlay.hidden = false;
      status.textContent = "Firmware Studio server is unreachable.";
    }
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    status.textContent = "Verifying credentials…";
    try {
      const result = await request("/api/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: password.value })
      });
      password.value = "";
      if (!result.ok) {
        status.textContent = result.code === 429 ? "Too many attempts. Try again later." : "Login failed.";
      } else {
        status.textContent = "";
        await refresh();
      }
    } catch (_) {
      status.textContent = "Connection failed.";
    } finally {
      button.disabled = false;
    }
  });
  logout.addEventListener("click", async () => {
    try {
      await request("/api/logout", { method: "POST", headers: { "X-CSRF-Token": csrf } });
      message("Session signed out");
    } finally {
      csrf = "";
      await refresh();
      password.focus();
    }
  });
  refresh();
})();
