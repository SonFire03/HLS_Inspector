const statusMessage = document.getElementById("status-message");
const form = document.getElementById("analyze-form");
const input = document.getElementById("url-input");
const dropzone = document.getElementById("dropzone");
const historyBody = document.getElementById("history-body");
const clearHistoryButton = document.getElementById("clear-history");

function setStatus(message, isError = false) {
  statusMessage.textContent = message;
  statusMessage.style.color = isError ? "#fecaca" : "";
}

function extractUrls(text) {
  const matches = text.match(/https?:\/\/[^\s'"<>]+/gi) || [];
  return [...new Set(matches.map((item) => item.trim()))];
}

function appendHistoryRow(item) {
  const row = document.createElement("tr");
  row.dataset.id = item.id;
  row.innerHTML = `
    <td>${item.id}</td>
    <td>${escapeHtml(item.page_title || "Sans titre")}</td>
    <td class="truncate"><a href="${escapeAttr(item.page_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.page_url)}</a></td>
    <td>
      ${item.m3u8_url ? `
        <div class="stream-cell">
          <code>${escapeHtml(item.m3u8_url)}</code>
          <button class="copy-button" type="button" data-copy="${escapeAttr(item.m3u8_url)}">Copier</button>
        </div>
      ` : '<span class="muted">Aucun</span>'}
    </td>
    <td>${escapeHtml(item.scanned_at)}</td>
    <td><span class="badge status-${escapeAttr(item.status)}">${escapeHtml(item.status)}</span></td>
    <td><button class="danger-button delete-row" type="button">Supprimer</button></td>
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

async function refreshHistory() {
  const response = await fetch("/api/history");
  const data = await response.json();
  historyBody.innerHTML = "";
  data.items.forEach(appendHistoryRow);
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

  if (target.classList.contains("copy-button")) {
    await navigator.clipboard.writeText(target.dataset.copy);
    setStatus("Lien .m3u8 copié dans le presse-papiers.");
  }

  if (target.classList.contains("delete-row")) {
    const row = target.closest("tr");
    const id = row?.dataset?.id;
    if (!id) return;
    if (!confirm("Supprimer cette entrée ?")) return;

    const response = await fetch(`/api/history/${id}`, { method: "DELETE" });
    if (response.ok) {
      row.remove();
      setStatus("Entrée supprimée.");
    } else {
      setStatus("Impossible de supprimer l’entrée.", true);
    }
  }
});

clearHistoryButton.addEventListener("click", async () => {
  if (!confirm("Vider tout l’historique ?")) return;
  const response = await fetch("/api/history", { method: "DELETE" });
  if (response.ok) {
    historyBody.innerHTML = "";
    setStatus("Historique vidé.");
  } else {
    setStatus("Impossible de vider l’historique.", true);
  }
});
