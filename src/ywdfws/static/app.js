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
  const inventoryButton = document.getElementById("refresh-inventory");
  const backups = document.getElementById("backup-list");
  const set = (id, value) => { document.getElementById(id).textContent = value; };
  async function loadInventory() {
    inventoryButton.disabled = true;
    set("inventory-source", "Reading qualified profile and stored backup records…");
    try {
      const result = await request("/api/inventory");
      if (!result.ok) throw new Error("Inventory request failed (" + result.code + ")");
      const data = result.body;
      const hw = data.target;
      set("hat-name", hw.mcu + " / " + hw.rf_chip);
      set("hat-spec", hw.tcxo_mhz + " MHz TCXO · " + (hw.flash_size_bytes / 1024) + " KiB · " + hw.uart);
      set("firmware-live", data.running_firmware.state === "not_probed" ? "Not probed" : "Unknown");
      set("backup-count", String(data.backup_count));
      const restricted = data.backup_access === "restricted";
      set("backup-access", restricted ? "Root backup directory restricted" : "Catalog checked");
      set("inventory-source", "Source: qualified on-disk hardware manifest; NOT a live HAT scan. Catalog: " +
        (data.catalog_access || "local") + ".");
      set("backup-explainer", restricted ? "Start the read-only root broker to view protected backups" :
        "SHA-256 validated against on-disk manifest");
      backups.replaceChildren();
      if (!data.backups.length) {
        const empty = document.createElement("div");
        empty.className = "backup-empty";
        empty.textContent = restricted ?
          "Protected backup folders are not accessible to the web user. Start the local read-only inventory broker." :
          "No matching backup records found in accessible storage.";
        backups.appendChild(empty);
      }
      for (const entry of data.backups) {
        const line = document.createElement("div");
        line.className = "backup-row";
        const main = document.createElement("div");
        const name = document.createElement("strong");
        name.textContent = entry.id;
        const details = document.createElement("small");
        const date = entry.captured_unix ? new Date(entry.captured_unix * 1000).toLocaleString() : "Date unknown";
        details.textContent = date + " · " + (entry.captured_identity || "Identity unavailable");
        main.append(name, details);
        const label = document.createElement("span");
        label.className = "backup-label " + (entry.integrity === "verified" ? "good" : "bad");
        label.textContent = entry.integrity === "verified" ?
          (entry.restore_qualified ? "GOLDEN STOCK · VERIFIED" : "CAPTURE · VERIFIED") : "INVALID / UNKNOWN";
        line.append(main, label);
        backups.appendChild(line);
      }
      message("FM1 inventory refreshed; " + data.backup_count + " catalog entries; live HAT probe not performed");
    } catch (error) {
      set("inventory-source", error.message);
      message("Inventory unavailable: " + error.message);
    } finally {
      inventoryButton.disabled = false;
    }
  }
  inventoryButton.addEventListener("click", loadInventory);

  async function refresh() {
    try {
      const result = await request("/api/session");
      if (result.ok && result.body.authenticated) {
        csrf = result.body.csrf;
        overlay.hidden = true;
        logout.hidden = false;
        state.textContent = "AUTHENTICATED / FM0";
        message("Authenticated browser session established; firmware writes disabled");
        await loadInventory();
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
