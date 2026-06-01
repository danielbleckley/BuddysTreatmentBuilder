const health = document.querySelector("#health");
const folder = document.querySelector("#folder");
const form = document.querySelector("#downloadForm");
const button = document.querySelector("#downloadButton");
const jobsEl = document.querySelector("#jobs");
const refreshButton = document.querySelector("#refreshButton");
const clearHistoryButton = document.querySelector("#clearHistoryButton");
const projectTitle = document.querySelector("#projectTitle");
const settingsForm = document.querySelector("#settingsForm");
const settingsButton = document.querySelector("#settingsButton");
const projectNameInput = document.querySelector("#projectName");
const downloadFolderInput = document.querySelector("#downloadFolder");
const chooseFolderButton = document.querySelector("#chooseFolderButton");
const newFolderForm = document.querySelector("#newFolderForm");
const newFolderButton = document.querySelector("#newFolderButton");
const newFolderNameInput = document.querySelector("#newFolderName");
const uploadForm = document.querySelector("#uploadForm");
const mediaFileInput = document.querySelector("#mediaFile");
const dropZone = document.querySelector("#dropZone");

let appReady = false;
let ffmpegReady = false;
let pollTimer = null;
const FRAME_STEP_SECONDS = 1 / 30;

function setHealth(status) {
  projectTitle.textContent = status.projectName || "Buddys Treatment Builder";
  document.title = `${projectTitle.textContent} Downloader`;
  projectNameInput.value = status.projectName || "";
  downloadFolderInput.value = status.downloadFolder || "";
  folder.textContent = status.downloadFolder;

  if (!status.folderReady) {
    health.textContent = "Folder blocked";
    health.className = "status warn";
    button.disabled = true;
    appReady = false;
    ffmpegReady = false;
    return;
  }

  if (status.ytDlpInstalled) {
    ffmpegReady = Boolean(status.ffmpegInstalled);
    health.textContent = status.ffmpegInstalled ? "Ready" : "Ready, ffmpeg optional";
    health.className = "status ready";
    button.disabled = false;
    appReady = true;
    return;
  }

  health.textContent = "Install yt-dlp";
  health.className = "status warn";
  button.disabled = true;
  appReady = false;
  ffmpegReady = Boolean(status.ffmpegInstalled);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => {
    const entities = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    };
    return entities[char];
  });
}

function formatTime(seconds) {
  if (!Number.isFinite(seconds)) {
    return "00:00.0";
  }
  const minutes = Math.floor(seconds / 60);
  const wholeSeconds = Math.floor(seconds % 60);
  const tenths = Math.floor((seconds % 1) * 10);
  return `${String(minutes).padStart(2, "0")}:${String(wholeSeconds).padStart(2, "0")}.${tenths}`;
}

function formatSizeMb(sizeMb) {
  if (!Number.isFinite(sizeMb) || sizeMb <= 0) {
    return "--";
  }
  return `${sizeMb.toFixed(sizeMb >= 10 ? 1 : 2)} MB`;
}

function mediaElement(preview) {
  return preview.querySelector("video, audio");
}

function clampTime(media, seconds) {
  const duration = Number.isFinite(media.duration) ? media.duration : Number.MAX_SAFE_INTEGER;
  return Math.max(0, Math.min(duration, seconds));
}

function updateMp4Estimates(preview) {
  const media = mediaElement(preview);
  const controls = preview.querySelector(".trim-grid");
  const sourceSizeBytes = Number(preview.dataset.sourceSize || 0);
  const duration = Number.isFinite(media.duration) ? media.duration : Number(preview.dataset.duration || 0);
  if (!controls || !sourceSizeBytes || !duration) {
    return;
  }

  const start = Math.max(0, Number(controls.querySelector("[data-trim-start]").value || 0));
  const endInput = Number(controls.querySelector("[data-trim-end]").value || duration);
  const end = Math.max(start, Math.min(duration, endInput || duration));
  const speed = Math.max(0.2, Number(controls.querySelector("[data-speed]").value || 1));
  const loops = Math.max(1, Math.round(Number(controls.querySelector("[data-loop-count]").value || 1)));
  const sourceSizeMb = sourceSizeBytes / 1024 / 1024;
  const sourceRateMbPerSecond = sourceSizeMb / duration;
  const trimmedDuration = Math.max(0.1, (end - start) / speed);
  const loopedDuration = trimmedDuration * loops;
  const trimmedEstimate = sourceRateMbPerSecond * trimmedDuration;
  const loopedEstimate = trimmedEstimate * loops;
  const percentInput = preview.querySelector("[data-compression-slider]");
  const percent = Math.max(10, Math.min(100, Number(percentInput.value || 100)));
  const compressedEstimate = loopedEstimate * (percent / 100);

  preview.querySelector("[data-est-full]").textContent = formatSizeMb(sourceSizeMb);
  preview.querySelector("[data-est-trimmed]").textContent = formatSizeMb(trimmedEstimate);
  preview.querySelector("[data-est-looped]").textContent = formatSizeMb(loopedEstimate);
  preview.querySelector("[data-est-compressed]").textContent = percent >= 100 ? "Full size" : formatSizeMb(compressedEstimate);
  preview.querySelector("[data-compression-value]").textContent = `${percent}%`;
  preview.dataset.targetSizeMb = percent >= 100 ? "" : String(compressedEstimate.toFixed(2));
}

function syncTransport(preview) {
  const media = mediaElement(preview);
  const scrubber = preview.querySelector("[data-scrubber]");
  const timecode = preview.querySelector("[data-timecode]");
  const playButton = preview.querySelector("[data-play-toggle]");
  const duration = Number.isFinite(media.duration) ? media.duration : 0;

  scrubber.max = duration || 0;
  scrubber.value = media.currentTime || 0;
  timecode.textContent = `${formatTime(media.currentTime)} / ${formatTime(duration)}`;
  playButton.textContent = media.paused ? "Play" : "Pause";
}

function renderJobs(jobs) {
  if (!jobs.length) {
    jobsEl.innerHTML = '<div class="empty">No downloads yet.</div>';
    return;
  }

  jobsEl.innerHTML = jobs
    .map((job) => {
      const progress = Math.round(Number(job.progress || 0));
      const progressBar = job.status === "queued" || job.status === "running"
        ? `
          <div class="progress">
            <div class="progress-track">
              <span style="width: ${Math.max(0, Math.min(100, progress))}%"></span>
            </div>
            <output>${Math.max(0, Math.min(100, progress))}%</output>
          </div>
        `
        : "";
      const logLines = Array.isArray(job.log) ? job.log.filter(Boolean) : [];
      const errorLog = job.status === "error" && logLines.length
        ? `
          <details class="error-log" open>
            <summary>Download error log</summary>
            <pre>${escapeHtml(logLines.join("\n"))}</pre>
          </details>
        `
        : "";
      const savedLink = job.savedUrl
        ? `<a class="saved-link" href="${escapeHtml(job.savedUrl)}" download>Download Saved File</a>`
        : "";
      const isAudio = job.mediaKind === "audio";
      const player = isAudio
        ? `<audio controls preload="metadata" src="${escapeHtml(job.mediaUrl)}"></audio>`
        : `<video controls preload="metadata" src="${escapeHtml(job.mediaUrl)}"></video>`;
      const muteControl = isAudio
        ? ""
        : `
              <label class="mute-option">
                <input type="checkbox" data-mute-save />
                Mute saved video
              </label>
        `;
      const formatControl = isAudio
        ? `
                <label>
                  Audio format
                  <select data-audio-format>
                    <option value="m4a">M4A / AAC</option>
                    <option value="mp3">MP3</option>
                    <option value="wav">WAV</option>
                    <option value="aiff">AIFF</option>
                  </select>
                </label>
        `
        : "";
      const gifPanel = isAudio
        ? ""
        : `
            <details class="gif-panel" open>
              <summary>GIF Builder</summary>
              <div class="gif-grid" data-job-id="${escapeHtml(job.id)}">
                <label>
                  Max file size MB
                  <input type="number" min="0.25" max="100" step="0.25" value="8" data-gif-size />
                </label>
                <label>
                  Max width
                  <input type="number" min="160" max="1920" step="10" value="640" data-gif-width />
                </label>
                <label>
                  Max height
                  <input type="number" min="120" max="1920" step="10" value="360" data-gif-height />
                </label>
                <label>
                  FPS
                  <input type="number" min="6" max="30" step="1" value="12" data-gif-fps />
                </label>
                <button type="button" data-save-gif ${ffmpegReady ? "" : "disabled"}>Export GIF</button>
              </div>
            </details>
        `;
      const preview = job.mediaUrl
        ? `
          <div class="preview ${isAudio ? "audio-preview" : ""}" data-source-size="${Number(job.fileSize || 0)}" data-media-kind="${escapeHtml(job.mediaKind || "video")}">
            ${player}
            <div class="transport">
              <button type="button" data-jump-back title="Jump back 5 seconds">-5s</button>
              <button type="button" data-frame-back title="Step back one frame">-1f</button>
              <button type="button" data-play-toggle>Play</button>
              <button type="button" data-frame-forward title="Step forward one frame">+1f</button>
              <button type="button" data-jump-forward title="Jump forward 5 seconds">+5s</button>
              <input type="range" min="0" step="0.01" value="0" data-scrubber />
              <output data-timecode>00:00.0 / 00:00.0</output>
            </div>
            <div class="trim-grid" data-job-id="${escapeHtml(job.id)}">
              <label>
                Start
                <input type="number" min="0" step="0.1" value="0" data-trim-start />
              </label>
              <button type="button" data-set-start>Set Start</button>
              <label>
                End
                <input type="number" min="0" step="0.1" placeholder="End time" data-trim-end />
              </label>
              <button type="button" data-set-end>Set End</button>
              ${muteControl}
              <label class="speed-option">
                Speed
                <input type="range" min="0.2" max="5" step="0.1" value="1" data-speed />
              </label>
              <output class="speed-value" data-speed-value>1.0x</output>
              <label>
                Loops
                <input type="number" min="1" max="50" step="1" value="1" data-loop-count />
              </label>
              <button type="button" data-save-original>Save Original</button>
              <button type="button" data-save-trim ${ffmpegReady ? "" : "disabled"}>Save Trim</button>
            </div>
            <details class="mp4-panel" open>
              <summary>${isAudio ? "Audio Export" : "MP4 Export"}</summary>
              <div class="mp4-grid">
                ${formatControl}
                <label class="compression-option">
                  Compression
                  <input type="range" min="10" max="100" step="5" value="100" data-compression-slider />
                </label>
                <output class="compression-value" data-compression-value>100%</output>
                <div class="mp4-estimates">
                  <span>Original file <strong data-est-full>--</strong></span>
                  <span>Trimmed full size <strong data-est-trimmed>--</strong></span>
                  <span>Looped full size <strong data-est-looped>--</strong></span>
                  <span>Compressed output <strong data-est-compressed>Full size</strong></span>
                </div>
              </div>
            </details>
            ${gifPanel}
          </div>
        `
        : "";

      return `
        <article class="job">
          <div class="job-head">
            <div class="job-url" title="${escapeHtml(job.url)}">${escapeHtml(job.url)}</div>
            <span class="badge ${escapeHtml(job.status)}">${escapeHtml(job.status)}</span>
          </div>
          <p class="message">${escapeHtml(job.message || "")}</p>
          ${progressBar}
          ${errorLog}
          ${savedLink}
          ${preview}
        </article>
      `;
    })
    .join("");
}

function hasActiveJobs(jobs) {
  return jobs.some((job) => job.status === "queued" || job.status === "running");
}

async function loadStatus() {
  const response = await fetch("/api/status");
  setHealth(await response.json());
}

async function loadJobs() {
  const response = await fetch("/api/jobs");
  const data = await response.json();
  const jobs = data.jobs || [];
  renderJobs(jobs);
  if (!hasActiveJobs(jobs)) {
    stopPolling();
  }
}

function startPolling() {
  if (pollTimer) {
    return;
  }
  pollTimer = setInterval(loadJobs, 1500);
}

function stopPolling() {
  if (!pollTimer) {
    return;
  }
  clearInterval(pollTimer);
  pollTimer = null;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(form);
  button.disabled = true;
  button.textContent = "Starting...";

  try {
    const response = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: formData.get("url"),
        quality: formData.get("quality"),
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Download could not start.");
    }
    form.reset();
    form.querySelector('input[value="best"]').checked = true;
    await loadJobs();
    startPolling();
  } catch (error) {
    renderJobs([
      {
        url: formData.get("url"),
        status: "error",
        message: error.message,
        log: [],
      },
    ]);
  } finally {
    button.disabled = !appReady;
    button.textContent = "Download";
  }
});

refreshButton.addEventListener("click", loadJobs);

clearHistoryButton.addEventListener("click", async () => {
  clearHistoryButton.disabled = true;
  clearHistoryButton.textContent = "Deleting...";
  try {
    const response = await fetch("/api/clear-jobs", { method: "POST" });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "History could not be deleted.");
    }
    await loadJobs();
  } catch (error) {
    health.textContent = error.message;
    health.className = "status warn";
  } finally {
    clearHistoryButton.disabled = false;
    clearHistoryButton.textContent = "Delete History";
  }
});

async function uploadMedia(file) {
  if (!file) {
    return;
  }

  const formData = new FormData();
  formData.append("mediaFile", file);
  dropZone.classList.remove("dragging");
  dropZone.querySelector("strong").textContent = "Loading media...";

  try {
    const response = await fetch("/api/upload", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Upload failed.");
    }
    uploadForm.reset();
    await loadJobs();
  } catch (error) {
    health.textContent = error.message;
    health.className = "status warn";
  } finally {
    dropZone.querySelector("strong").textContent = "Drop media here";
  }
}

mediaFileInput.addEventListener("change", () => {
  uploadMedia(mediaFileInput.files[0]);
});

dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropZone.classList.add("dragging");
});

dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("dragging");
});

dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  const file = event.dataTransfer.files[0];
  uploadMedia(file);
});

jobsEl.addEventListener("loadedmetadata", (event) => {
  if (!["VIDEO", "AUDIO"].includes(event.target.tagName)) {
    return;
  }
  const preview = event.target.closest(".preview");
  const endInput = preview.querySelector("[data-trim-end]");
  if (!endInput.value && Number.isFinite(event.target.duration)) {
    endInput.value = event.target.duration.toFixed(1);
  }
  if (Number.isFinite(event.target.duration)) {
    preview.dataset.duration = String(event.target.duration);
  }
  const speedInput = preview.querySelector("[data-speed]");
  if (speedInput) {
    event.target.playbackRate = Number(speedInput.value);
  }
  updateMp4Estimates(preview);
  syncTransport(preview);
}, true);

jobsEl.addEventListener("timeupdate", (event) => {
  if (["VIDEO", "AUDIO"].includes(event.target.tagName)) {
    syncTransport(event.target.closest(".preview"));
  }
}, true);

jobsEl.addEventListener("play", (event) => {
  if (["VIDEO", "AUDIO"].includes(event.target.tagName)) {
    syncTransport(event.target.closest(".preview"));
  }
}, true);

jobsEl.addEventListener("pause", (event) => {
  if (["VIDEO", "AUDIO"].includes(event.target.tagName)) {
    syncTransport(event.target.closest(".preview"));
  }
}, true);

jobsEl.addEventListener("input", (event) => {
  if (event.target.matches("[data-compression-slider]")) {
    updateMp4Estimates(event.target.closest(".preview"));
    return;
  }

  if (event.target.matches("[data-speed]")) {
    const preview = event.target.closest(".preview");
    const media = mediaElement(preview);
    const speed = Number(event.target.value);
    preview.querySelector("[data-speed-value]").textContent = `${speed.toFixed(1)}x`;
    media.playbackRate = speed;
    updateMp4Estimates(preview);
    return;
  }

  if (event.target.matches("[data-trim-start], [data-trim-end], [data-loop-count]")) {
    updateMp4Estimates(event.target.closest(".preview"));
    return;
  }

  if (!event.target.matches("[data-scrubber]")) {
    return;
  }
  const preview = event.target.closest(".preview");
  const media = mediaElement(preview);
  media.currentTime = Number(event.target.value);
  syncTransport(preview);
});

jobsEl.addEventListener("click", async (event) => {
  const transportButton = event.target.closest(
    "[data-jump-back], [data-frame-back], [data-play-toggle], [data-frame-forward], [data-jump-forward]"
  );
  if (transportButton) {
    const preview = transportButton.closest(".preview");
    const media = mediaElement(preview);

    if (transportButton.hasAttribute("data-play-toggle")) {
      if (media.paused) {
        await media.play();
      } else {
        media.pause();
      }
      syncTransport(preview);
      return;
    }

    media.pause();
    if (transportButton.hasAttribute("data-jump-back")) {
      media.currentTime = clampTime(media, media.currentTime - 5);
    }
    if (transportButton.hasAttribute("data-frame-back")) {
      media.currentTime = clampTime(media, media.currentTime - FRAME_STEP_SECONDS);
    }
    if (transportButton.hasAttribute("data-frame-forward")) {
      media.currentTime = clampTime(media, media.currentTime + FRAME_STEP_SECONDS);
    }
    if (transportButton.hasAttribute("data-jump-forward")) {
      media.currentTime = clampTime(media, media.currentTime + 5);
    }
    syncTransport(preview);
    return;
  }

  const trimPointButton = event.target.closest("[data-set-start], [data-set-end]");
  if (trimPointButton) {
    const preview = trimPointButton.closest(".preview");
    const media = mediaElement(preview);
    const targetInput = trimPointButton.hasAttribute("data-set-start")
      ? preview.querySelector("[data-trim-start]")
      : preview.querySelector("[data-trim-end]");
    targetInput.value = media.currentTime.toFixed(1);
    updateMp4Estimates(preview);
    return;
  }

  const gifAction = event.target.closest("[data-save-gif]");
  if (gifAction) {
    const preview = gifAction.closest(".preview");
    const controls = preview.querySelector(".trim-grid");
    const gifControls = gifAction.closest(".gif-grid");
    const payload = {
      jobId: gifControls.dataset.jobId,
      start: controls.querySelector("[data-trim-start]").value,
      end: controls.querySelector("[data-trim-end]").value,
      maxSizeMb: gifControls.querySelector("[data-gif-size]").value,
      maxWidth: gifControls.querySelector("[data-gif-width]").value,
      maxHeight: gifControls.querySelector("[data-gif-height]").value,
      fps: gifControls.querySelector("[data-gif-fps]").value,
    };

    gifAction.disabled = true;
    gifAction.textContent = "Exporting...";
    try {
      const response = await fetch("/api/gif", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || "GIF export failed.");
      }
      await loadJobs();
    } catch (error) {
      const message = preview.closest(".job").querySelector(".message");
      message.textContent = error.message;
    } finally {
      gifAction.disabled = false;
      gifAction.textContent = "Export GIF";
    }
    return;
  }

  const action = event.target.closest("[data-save-original], [data-save-trim]");
  if (!action) {
    return;
  }

  const controls = action.closest(".trim-grid");
  const jobId = controls.dataset.jobId;
  const isTrim = action.hasAttribute("data-save-trim");
  const endpoint = isTrim ? "/api/trim" : "/api/save-original";
  const payload = {
    jobId,
    mute: Boolean(controls.querySelector("[data-mute-save]")?.checked),
  };
  const preview = controls.closest(".preview");
  payload.audioFormat = preview.querySelector("[data-audio-format]")?.value || "m4a";

  if (isTrim) {
    payload.start = controls.querySelector("[data-trim-start]").value;
    payload.end = controls.querySelector("[data-trim-end]").value;
    payload.speed = controls.querySelector("[data-speed]").value;
    payload.loopCount = controls.querySelector("[data-loop-count]").value;
    payload.targetSizeMb = preview.dataset.targetSizeMb || "";
  }

  action.disabled = true;
  action.textContent = isTrim ? "Saving..." : "Copying...";
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Save failed.");
    }
    await loadJobs();
  } catch (error) {
    const message = controls.closest(".job").querySelector(".message");
    message.textContent = error.message;
  } finally {
    action.disabled = false;
    action.textContent = isTrim ? "Save Trim" : "Save Original";
  }
});

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  settingsButton.disabled = true;
  settingsButton.textContent = "Saving...";

  try {
    const response = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        projectName: projectNameInput.value,
        downloadFolder: downloadFolderInput.value,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Settings could not be saved.");
    }
    await loadStatus();
  } catch (error) {
    health.textContent = error.message;
    health.className = "status warn";
  } finally {
    settingsButton.disabled = false;
    settingsButton.textContent = "Save Settings";
    button.disabled = !appReady;
  }
});

chooseFolderButton.addEventListener("click", async () => {
  chooseFolderButton.disabled = true;
  chooseFolderButton.textContent = "Choosing...";

  try {
    const response = await fetch("/api/pick-folder", {
      method: "POST",
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Folder could not be selected.");
    }
    await loadStatus();
  } catch (error) {
    health.textContent = error.message;
    health.className = "status warn";
  } finally {
    chooseFolderButton.disabled = false;
    chooseFolderButton.textContent = "Choose Folder";
    button.disabled = !appReady;
  }
});

newFolderForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  newFolderButton.disabled = true;
  newFolderButton.textContent = "Creating...";

  try {
    const response = await fetch("/api/create-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        folderName: newFolderNameInput.value,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Folder could not be created.");
    }
    newFolderForm.reset();
    await loadStatus();
  } catch (error) {
    health.textContent = error.message;
    health.className = "status warn";
  } finally {
    newFolderButton.disabled = false;
    newFolderButton.textContent = "Create Folder";
    button.disabled = !appReady;
  }
});

async function init() {
  await loadStatus();
  await loadJobs();
}

init();
