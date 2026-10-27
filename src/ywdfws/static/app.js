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
  let lastJob = null;
  let pollTimer = null;
  const preflightButton = document.getElementById("start-preflight");
  const meter = document.getElementById("preflight-meter");
  const progressBox = document.getElementById("job-progress");
  const jobSummary = document.getElementById("job-summary");
  const seenEvents = new Set();

  function stopPolling() {
    if (pollTimer !== null) clearTimeout(pollTimer);
    pollTimer = null;
  }
  function renderJob(job) {
    progressBox.hidden = false;
    meter.value = job.steps_completed;
    set("job-count", job.steps_completed + " / " + job.steps_total + " checks");
    set("job-state", job.status === "running" ? "Checks in progress…" :
        job.status === "complete" ? "Catalog preflight complete" :
        job.status === "limited" ? "Catalog access restricted" : "Preflight failed");
    for (const item of job.events) {
      const key = job.id + ":" + item.seq;
      if (!seenEvents.has(key)) {
        seenEvents.add(key);
        message("[PREFLIGHT] " + item.message);
      }
    }
    if (job.result) {
      jobSummary.textContent =
        "Verified stock backups: " + (job.result.verified_golden_stock === null ? "unknown (restricted)" : job.result.verified_golden_stock) + " · " +
        "Catalog: " + job.result.catalog_source + ". " + job.result.next_action;
    } else if (job.status === "failed") {
      jobSummary.textContent = "Read-only checks failed. No HAT operation was performed.";
    } else {
      jobSummary.textContent = "Checking stored metadata only. No modem UART or GPIO access.";
    }
    preflightButton.disabled = job.status === "running";
  }
  async function pollJob(id) {
    try {
      const result = await request("/api/jobs/" + encodeURIComponent(id));
      if (!result.ok) throw new Error("Preflight status unavailable");
      if (lastJob !== id) return;
      renderJob(result.body);
      if (result.body.status === "running") {
        pollTimer = setTimeout(() => pollJob(id), 650);
      }
    } catch (error) {
      if (lastJob === id) {
        jobSummary.textContent = "Preflight status unavailable; refresh the page or sign in again.";
        preflightButton.disabled = false;
      }
    }
  }
  preflightButton.addEventListener("click", async () => {
    if (!csrf) return;
    preflightButton.disabled = true;
    stopPolling();
    seenEvents.clear();
    progressBox.hidden = false;
    meter.value = 0;
    set("job-state", "Starting read-only preflight");
    jobSummary.textContent = "Requesting only on-disk inventory checks.";
    try {
      const result = await request("/api/jobs/preflight", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf },
      });
      if (!result.ok) throw new Error(result.body.error || "Preflight rejected");
      lastJob = result.body.id;
      renderJob(result.body);
      await pollJob(lastJob);
    } catch (error) {
      jobSummary.textContent = error.message;
      preflightButton.disabled = false;
    }
  });


  // FM1.5c: separate HTTPS-only root-broker backup. No generic command endpoint.
  const refreshWorkerButton = document.getElementById("refresh-worker");
  const backupForm = document.getElementById("hardware-backup-form");
  const approvalInput = document.getElementById("backup-approval");
  const phraseInput = document.getElementById("backup-phrase");
  const startBackupButton = document.getElementById("backup-start");
  const backupBox = document.getElementById("backup-operation");
  const backupMeter = document.getElementById("backup-meter");
  const backupJobSummary = document.getElementById("backup-job-summary");
  let hardwareJobId = null;
  let hardwarePoll = null;
  const hardwareEvents = new Set();
  function stopHardwarePoll() {
    if (hardwarePoll !== null) clearTimeout(hardwarePoll);
    hardwarePoll = null;
  }
  async function checkHardwareWorker() {
    backupForm.hidden = true;
    refreshWorkerButton.disabled = true;
    set("backup-worker-state", "Checking root maintenance authorization…");
    try {
      const result = await request("/api/maintenance/ready");
      if (!result.ok) throw new Error("Worker check failed (" + result.code + ")");
      if (!result.body.available) {
        const reason = result.body.reason || "No armed maintenance worker";
        set("backup-worker-state", "Locked: " + reason + ". HTTPS and a root-terminal one-time code are required.");
      } else {
        set("backup-worker-state", "Root worker armed. One operation permitted. Enter the exact code shown in its root terminal.");
        backupForm.hidden = false;
      }
    } catch (error) {
      set("backup-worker-state", "Maintenance worker unavailable: " + error.message);
    } finally {
      refreshWorkerButton.disabled = !csrf;
    }
  }
  function showHardwareJob(job) {
    backupBox.hidden = false;
    backupMeter.value = job.steps_completed;
    set("backup-job-count", job.steps_completed + " / " + job.steps_total + " stages");
    set("backup-job-state", job.status === "running" ? "HAT backup in progress" :
        job.status === "completed" ? "Backup verified" : "Backup failed / recovery required");
    for (const event of job.events || []) {
      const key = job.id + ":" + event.seq;
      if (!hardwareEvents.has(key)) {
        hardwareEvents.add(key);
        message("[HAT BACKUP] " + event.message);
      }
    }
    if (job.status === "completed") {
      backupJobSummary.textContent = "Two full-flash reads verified. Type: " +
        job.backup_kind + "; SHA-256: " + job.sha256 +
        ". Flash and option bytes were not written.";
    } else if (job.status === "failed") {
      backupJobSummary.textContent =
        "Backup failed. Inspect the root worker terminal, verify modem service recovery and preserve the original stock backup.";
    } else {
      backupJobSummary.textContent =
        "Do not stop the worker or power-cycle the Pi. A firmware READ and HAT reset may be in progress.";
    }
  }
  async function pollHardwareJob(id) {
    try {
      const result = await request("/api/maintenance/backup/" + encodeURIComponent(id));
      if (!result.ok) throw new Error(result.body.error || "Backup worker not reachable");
      if (hardwareJobId !== id) return;
      showHardwareJob(result.body);
      if (result.body.status === "running") {
        hardwarePoll = setTimeout(() => pollHardwareJob(id), 900);
      }
    } catch (error) {
      if (hardwareJobId === id) {
        backupJobSummary.textContent = "Status unavailable: " + error.message +
          ". The backup may STILL be running; check the root worker terminal before retrying.";
        message("[HAT BACKUP] Lost worker status; no automatic retry.");
      }
    }
  }
  backupForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!csrf || hardwareJobId) return;
    startBackupButton.disabled = true;
    backupBox.hidden = false;
    backupMeter.value = 0;
    set("backup-job-state", "Requesting root authorization");
    backupJobSummary.textContent = "Submitting one-time approval to the separate root worker.";
    const approval = approvalInput.value;
    const confirmation = phraseInput.value;
    approvalInput.value = "";
    phraseInput.value = "";
    try {
      const result = await request("/api/maintenance/backup", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ authorization: approval, confirmation }),
      });
      if (!result.ok) {
        backupJobSummary.textContent = result.body.error || "Backup authorization rejected";
        message("[HAT BACKUP] Authorization rejected; no automatic retry.");
        return;
      }
      hardwareJobId = result.body.id;
      backupForm.hidden = true;
      showHardwareJob(result.body);
      await pollHardwareJob(hardwareJobId);
    } catch (error) {
      backupJobSummary.textContent = "Request failed: " + error.message +
        ". Check root worker status before any attempt to retry.";
    } finally {
      startBackupButton.disabled = false;
    }
  });
  refreshWorkerButton.addEventListener("click", checkHardwareWorker);

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
      const restricted = data.backup_access === "restricted";
      set("backup-count", restricted ? "Unknown" : String(data.backup_count));
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
      message("FM1 inventory refreshed; " + (restricted ? "root backup count unknown" : data.backup_count + " catalog entries") + "; live HAT probe not performed");
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
        state.textContent = "AUTHENTICATED / FM1.5";
        preflightButton.disabled = false;
        refreshWorkerButton.disabled = false;
        message("Authenticated browser session established; firmware writes disabled");
        await checkHardwareWorker();
        await loadInventory();
      } else {
        csrf = "";
        overlay.hidden = false;
        logout.hidden = true;
        state.textContent = "LOCKED";
        stopPolling();
        lastJob = null;
        preflightButton.disabled = true;
        refreshWorkerButton.disabled = true;
        backupForm.hidden = true;
        stopHardwarePoll();
        hardwareJobId = null;
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
      stopPolling();
      lastJob = null;
      await refresh();
      password.focus();
    }
  });
  refresh();
})();
