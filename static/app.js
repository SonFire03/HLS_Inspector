const statusMessage = document.getElementById("status-message");
const form = document.getElementById("analyze-form");
const input = document.getElementById("url-input");
const dropzone = document.getElementById("dropzone");
const historyBody = document.getElementById("history-body");
const clearHistoryButton = document.getElementById("clear-history");
const detailsModal = document.getElementById("details-modal");
const detailsTitle = document.getElementById("details-title");
const detailsUrl = document.getElementById("details-url");
const detailsStatus = document.getElementById("details-status");
const detailsDate = document.getElementById("details-date");
const detailsError = document.getElementById("details-error");
const detailsStreams = document.getElementById("details-streams");
const detailsTrace = document.getElementById("details-trace");
const detailsSourceType = document.getElementById("details-source-type");
const detailsVideos = document.getElementById("details-videos");
const historySearch = document.getElementById("history-search");
const historyStatus = document.getElementById("history-status");
const historyMedia = document.getElementById("history-media");
const historyPerPage = document.getElementById("history-per-page");
const historyApply = document.getElementById("history-apply");
const historyReset = document.getElementById("history-reset");
const historyPrev = document.getElementById("history-prev");
const historyNext = document.getElementById("history-next");
const historyPage = document.getElementById("history-page");
const historyCache = new Map();
const historyMeta = document.getElementById("history-meta");
const statAnalyses = document.getElementById("stat-analyses");
const statStreams = document.getElementById("stat-streams");
const statVideos = document.getElementById("stat-videos");
const statDirect = document.getElementById("stat-direct");
const statSources = document.getElementById("stat-sources");

const historyState = {
  page: 1,
  perPage: Number(historyPerPage?.value || 10),
  status: historyStatus?.value || "all",
  media: historyMedia?.value || "all",
  search: historySearch?.value || "",
  grouped: true,
};

function setStatus(message, isError = false) {
  statusMessage.textContent = message;
  statusMessage.style.color = isError ? "#fecaca" : "";
}

function extractUrls(text) {
  const matches = text.match(/https?:\/\/[^\s'"<>]+/gi) || [];
  return [...new Set(matches.map((item) => item.trim()))];
}

function appendHistoryRow(item) {
  historyCache.set(String(item.id), item);
  const row = document.createElement("tr");
  row.dataset.id = item.id;
  const streams = Array.isArray(item.streams) ? item.streams : [];
  const videos = Array.isArray(item.videos) ? item.videos : [];
  row.innerHTML = `
    <td>${item.id}</td>
    <td>${escapeHtml(item.page_title || "Sans titre")}</td>
    <td class="truncate"><a href="${escapeAttr(item.page_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.page_url)}</a></td>
    <td>
      ${streams.length ? `
        <div class="stream-cell">
          ${streams.map((stream) => `
            <div class="stream-pill">
              <code>${escapeHtml(stream)}</code>
              <button class="copy-button" type="button" data-copy="${escapeAttr(stream)}">Copier</button>
            </div>
          `).join("")}
        </div>
      ` : '<span class="muted">Aucun</span>'}
    </td>
    <td>
      ${videos.length ? `
        <div class="stream-cell">
          ${videos.map((video) => `
            <div class="stream-pill">
              <code>${escapeHtml(video)}</code>
              <button class="copy-button" type="button" data-copy="${escapeAttr(video)}">Copier</button>
            </div>
          `).join("")}
        </div>
      ` : '<span class="muted">Aucun</span>'}
    </td>
    <td><span class="badge badge-source source-${escapeAttr(item.source_type || "unknown")}">${escapeHtml(item.source_label || "Inconnue")}</span></td>
    <td>${escapeHtml(item.scanned_at)}</td>
    <td><span class="badge status-${escapeAttr(item.status)}">${escapeHtml(item.status)}</span></td>
    <td>
      <button class="ghost-button details-row" type="button">Détails</button>
      <button class="danger-button delete-row" type="button">Supprimer</button>
    </td>
  `;
  historyBody.prepend(row);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#96;");
}

async function analyzeOne(url) {
  const response = await fetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  const data = await response.json();
  if (!response.ok && !data.status) {
    throw new Error("Erreur serveur");
  }
  return data;
}

function buildHistoryQuery() {
  const params = new URLSearchParams({
    page: String(historyState.page),
    per_page: String(historyState.perPage),
    status: historyState.status,
    media: historyState.media,
    search: historyState.search,
    grouped: historyState.grouped ? "1" : "0",
  });
  return params.toString();
}

async function refreshHistory() {
  const response = await fetch(`/api/history?${buildHistoryQuery()}`);
  const data = await response.json();
  historyBody.innerHTML = "";
  historyCache.clear();
  data.items.forEach(appendHistoryRow);
  updateHeroStats(data.items, data.pagination);

  const pagination = data.pagination || {};
  const totalItems = pagination.total_items ?? data.items.length;
  historyMeta.textContent = `${totalItems} analyse(s)`;
  historyPage.textContent = `Page ${pagination.page || 1} / ${pagination.total_pages || 1}`;
  historyPrev.disabled = !pagination.has_previous;
  historyNext.disabled = !pagination.has_next;
}

function updateHeroStats(items, pagination) {
  const totalAnalyses = pagination?.total_items ?? items.length;
  const totalStreams = items.reduce((sum, item) => sum + Number(item.stream_count || 0), 0);
  const totalVideos = items.reduce((sum, item) => sum + Number(item.video_count || 0), 0);
  const directCount = items.filter((item) => item.source_type === "direct").length;
  const sourceTypes = new Set(items.map((item) => item.source_type || "unknown"));

  statAnalyses.textContent = String(totalAnalyses);
  statStreams.textContent = String(totalStreams);
  if (statVideos) {
    statVideos.textContent = String(totalVideos);
  }
  statDirect.textContent = String(directCount);
  statSources.textContent = String(sourceTypes.size);
}

function openDetails(item) {
  detailsTitle.textContent = item.page_title || "Sans titre";
  detailsUrl.textContent = item.page_url || "-";
  detailsStatus.textContent = item.status || "-";
  detailsDate.textContent = item.scanned_at || "-";
  detailsError.textContent = item.error_message || "Aucune";
  detailsSourceType.textContent = item.source_label || "Inconnue";
  detailsSourceType.className = `badge badge-source source-${escapeAttr(item.source_type || "unknown")}`;

  detailsStreams.innerHTML = "";
  const streams = Array.isArray(item.streams) ? item.streams : [];
  if (streams.length) {
    streams.forEach((stream) => {
      const wrapper = document.createElement("div");
      wrapper.className = "stream-pill";
      const code = document.createElement("code");
      code.textContent = stream;
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "copy-button";
      copy.dataset.copy = stream;
      copy.textContent = "Copier";
      wrapper.append(code, copy);
      detailsStreams.appendChild(wrapper);
    });
  } else {
    detailsStreams.textContent = item.status === "success" ? "Flux non présent sur cette ligne." : "Aucun";
  }

  if (detailsVideos) {
    detailsVideos.innerHTML = "";
    const videos = Array.isArray(item.videos) ? item.videos : [];
    if (videos.length) {
      videos.forEach((video) => {
        const wrapper = document.createElement("div");
        wrapper.className = "stream-pill";
        const code = document.createElement("code");
        code.textContent = video;
        const copy = document.createElement("button");
        copy.type = "button";
        copy.className = "copy-button";
        copy.dataset.copy = video;
        copy.textContent = "Copier";
        wrapper.append(code, copy);
        detailsVideos.appendChild(wrapper);
      });
    } else {
      detailsVideos.textContent = item.status === "success" ? "Vidéos non présentes sur cette ligne." : "Aucune";
    }
  }

  detailsTrace.innerHTML = "";
  const trace = parseTrace(item.source_trace);
  if (trace.length === 0) {
    const li = document.createElement("li");
    li.textContent = "Aucune trace disponible.";
    detailsTrace.appendChild(li);
  } else {
    trace.forEach((step) => {
      const li = document.createElement("li");
      const parts = [step.stage];
      if (step.kind) parts.push(`kind=${step.kind}`);
      if (step.url) parts.push(step.url);
      if (step.message) parts.push(step.message);
      if (typeof step.count !== "undefined") parts.push(`count=${step.count}`);
      if (typeof step.bytes !== "undefined") parts.push(`bytes=${step.bytes}`);
      if (typeof step.streams !== "undefined") parts.push(`streams=${step.streams}`);
      li.textContent = parts.join(" · ");
      detailsTrace.appendChild(li);
    });
  }

  detailsModal.classList.remove("hidden");
  detailsModal.setAttribute("aria-hidden", "false");
}

function parseTrace(rawTrace) {
  if (!rawTrace) return [];
  if (Array.isArray(rawTrace)) return rawTrace;
  if (typeof rawTrace === "string") {
    try {
      const parsed = JSON.parse(rawTrace);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }
  return [];
}

function closeDetails() {
  detailsModal.classList.add("hidden");
  detailsModal.setAttribute("aria-hidden", "true");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const urls = extractUrls(input.value);

  if (urls.length === 0) {
    setStatus("Ajoute une URL HTTP ou HTTPS valide.", true);
    return;
  }

  setStatus(`Analyse de ${urls.length} URL(s) en cours...`);
  form.querySelector("button[type='submit']").disabled = true;

  try {
    for (const url of urls) {
      const result = await analyzeOne(url);
      if (result.status === "success") {
        setStatus(`Flux trouvé pour ${result.page_url}`);
      } else if (result.status === "no_stream_found") {
        setStatus(`Aucun flux trouvé pour ${result.page_url}`);
      } else {
        setStatus(result.error_message || `Erreur pour ${url}`, true);
      }
    }
    historyState.page = 1;
    await refreshHistory();
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    form.querySelector("button[type='submit']").disabled = false;
  }
});

dropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropzone.classList.add("dragover");
});

dropzone.addEventListener("dragleave", () => {
  dropzone.classList.remove("dragover");
});

dropzone.addEventListener("drop", async (event) => {
  event.preventDefault();
  dropzone.classList.remove("dragover");

  const text = event.dataTransfer.getData("text/plain");
  if (text) {
    input.value = input.value ? `${input.value}\n${text}` : text;
    return;
  }

  const file = event.dataTransfer.files && event.dataTransfer.files[0];
  if (file && file.name.toLowerCase().endsWith(".txt")) {
    const content = await file.text();
    input.value = input.value ? `${input.value}\n${content}` : content;
  }
});

dropzone.addEventListener("click", () => input.focus());

historyBody.addEventListener("click", async (event) => {
  const target = event.target;
  const row = target.closest("tr");
  const item = row ? historyCache.get(String(row.dataset.id)) : null;

  if (target.classList.contains("details-row") && item) {
    openDetails(item);
    return;
  }

  if (target.classList.contains("delete-row")) {
    const row = target.closest("tr");
    const id = row?.dataset?.id;
    if (!id) return;
    if (!confirm("Supprimer cette entrée ?")) return;

    const response = await fetch(`/api/history/${id}`, { method: "DELETE" });
    if (response.ok) {
      setStatus("Analyse supprimée.");
      await refreshHistory();
    } else {
      setStatus("Impossible de supprimer l’analyse.", true);
    }
  }
});

document.addEventListener("click", async (event) => {
  const target = event.target;
  if (!target.classList.contains("copy-button")) return;
  await navigator.clipboard.writeText(target.dataset.copy);
  if (statusMessage) {
    setStatus("Lien copié dans le presse-papiers.");
  }
});

detailsModal.addEventListener("click", (event) => {
  if (event.target.matches("[data-close-modal]")) {
    closeDetails();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !detailsModal.classList.contains("hidden")) {
    closeDetails();
  }
});

clearHistoryButton.addEventListener("click", async () => {
  if (!confirm("Vider tout l’historique ?")) return;
  const response = await fetch("/api/history", { method: "DELETE" });
  if (response.ok) {
    historyState.page = 1;
    historyBody.innerHTML = "";
    setStatus("Historique vidé.");
    historyCache.clear();
    await refreshHistory();
  } else {
    setStatus("Impossible de vider l’historique.", true);
  }
});

historyApply.addEventListener("click", async () => {
  historyState.page = 1;
  historyState.search = historySearch.value.trim();
  historyState.status = historyStatus.value;
  historyState.media = historyMedia.value;
  historyState.perPage = Number(historyPerPage.value || 10);
  await refreshHistory();
});

historyReset.addEventListener("click", async () => {
  historySearch.value = "";
  historyStatus.value = "all";
  historyMedia.value = "all";
  historyPerPage.value = "10";
  historyState.page = 1;
  historyState.search = "";
  historyState.status = "all";
  historyState.media = "all";
  historyState.perPage = 10;
  await refreshHistory();
});

historyPrev.addEventListener("click", async () => {
  if (historyState.page > 1) {
    historyState.page -= 1;
    await refreshHistory();
  }
});

historyNext.addEventListener("click", async () => {
  historyState.page += 1;
  await refreshHistory();
});

refreshHistory();
