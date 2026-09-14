const $ = id => document.getElementById(id);
const markdownInput = $("markdownInput"), filenameInput = $("filename"), generateButton = $("generateButton");
const reader = $("reader"), unitCount = $("unitCount"), generationCard = $("generationCard");
const progressMessage = $("progressMessage"), progressPercent = $("progressPercent"), progressBar = $("progressBar");
const playerDock = $("playerDock"), audioPlayer = $("audioPlayer"), playButton = $("playButton");
const previousButton = $("previousButton"), nextButton = $("nextButton");
const rewindButton = $("rewindButton"), forwardButton = $("forwardButton"), seekBar = $("seekBar");
const currentTimeLabel = $("currentTime"), durationLabel = $("duration"), speedSelect = $("speedSelect");
const downloadButton = $("downloadButton"), generationSelect = $("generationSelect");
const openGenerationButton = $("openGenerationButton");
const markdownPreview = $("markdownPreview"), previewTab = $("previewTab"), speechPlanTab = $("speechPlanTab");
const markdownDropZone = $("markdownDropZone"), markdownFileInput = $("markdownFileInput");
const openMarkdownButton = $("openMarkdownButton"), fileError = $("fileError");

let previewTimer, pollTimer, activeJobId, activeGenerationId, pendingPlayback;
let timeline = [], activeUnitIndex = -1, lastPlaybackSave = 0, playbackSaveInFlight = false;
let playbackSavePending = false;
let regenerationAvailable = false, regenerationBusy = false;
let previewSequence = 0, activePreviewTab = "preview", filenameWasEdited = false, dragDepth = 0;

function escapeHtml(value) {
    return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
function formatTime(seconds) {
    if (!Number.isFinite(seconds)) return "0:00";
    const total = Math.max(0, Math.floor(seconds));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
function unitId(entry) { return Number(entry.unit_id ?? entry.index ?? entry.id); }
function timelineIndexAt(position) {
    if (!timeline.length) return -1;
    const current = Math.max(0, Number(position) || 0);
    for (let index = 0; index < timeline.length; index += 1) {
        const nextStart = index + 1 < timeline.length ? Number(timeline[index + 1].start_seconds) : Infinity;
        if (current < nextStart) return index;
    }
    return timeline.length - 1;
}

function renderUnits(units, useTimeline = false) {
    if (!units || !units.length) {
        reader.className = "reader empty-reader";
        reader.innerHTML = '<div class="empty-state"><div class="empty-icon">¶</div><strong>Aguardando Markdown</strong><p>As frases que serão narradas aparecerão aqui.</p></div>';
        unitCount.textContent = "0 frases";
        return;
    }
    reader.className = "reader";
    unitCount.textContent = `${units.length} ${units.length === 1 ? "unidade" : "unidades"}`;
    reader.innerHTML = units.map((unit, index) => {
        const kind = unit.kind === "heading" ? "heading" : (unit.kind === "list" ? "list" : "");
        const text = unit.text ?? unit.display_text ?? "";
        const start = useTimeline ? Number(unit.start_seconds) : "";
        const action = useTimeline ? `<button class="regenerate-button" data-unit-id="${unitId(unit)}" ${regenerationAvailable ? "" : "disabled"} title="${regenerationAvailable ? "Regenerar somente esta unidade" : "Esta geração foi criada antes do suporte a regeneração individual"}">Regenerar</button>` : "";
        return `<div class="reader-unit ${kind}" tabindex="${useTimeline ? 0 : -1}" data-index="${index}" data-start="${start}"><span>${escapeHtml(text)}</span>${action}</div>`;
    }).join("");
    if (useTimeline) bindReaderClicks();
}
function seekTo(seconds, save = true) {
    if (!Number.isFinite(audioPlayer.duration)) return;
    audioPlayer.currentTime = Math.min(Math.max(0, seconds), audioPlayer.duration);
    updateActiveUnit(audioPlayer.currentTime);
    if (save) savePlayback(true);
}
function bindReaderClicks() {
    document.querySelectorAll(".reader-unit").forEach(element => {
        const activate = () => {
            seekTo(Number(element.dataset.start));
            audioPlayer.play().catch(console.error);
        };
        element.addEventListener("click", activate);
        element.addEventListener("keydown", event => { if (event.key === "Enter") activate(); });
    });
    document.querySelectorAll(".regenerate-button").forEach(button => {
        button.addEventListener("click", event => {
            event.stopPropagation();
            regenerateUnit(Number(button.dataset.unitId));
        });
    });
}
async function updatePreview() {
    const markdown = markdownInput.value.trim();
    const sequence = ++previewSequence;
    if (!markdown) {
        renderUnits([]);
        markdownPreview.innerHTML = '<div class="empty-state"><strong>Aguardando Markdown</strong></div>';
        return selectPreviewTab(activePreviewTab);
    }
    try {
        const options = {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({markdown})};
        const [planResponse, previewResponse] = await Promise.all([
            fetch("/api/plan", options), fetch("/api/preview", options)
        ]);
        if (!planResponse.ok || !previewResponse.ok) throw new Error("Preview inválido.");
        const [plan, preview] = await Promise.all([planResponse.json(), previewResponse.json()]);
        if (sequence !== previewSequence) return;
        renderUnits(plan.units, false);
        markdownPreview.innerHTML = preview.html;
        selectPreviewTab(activePreviewTab);
    } catch (error) { console.error(error); }
}

async function updateMarkdownPreview(markdown) {
    try {
        const response = await fetch("/api/preview", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({markdown})});
        if (!response.ok) throw new Error("Preview inválido.");
        markdownPreview.innerHTML = (await response.json()).html;
        selectPreviewTab(activePreviewTab);
    } catch (error) { console.error(error); }
}

function selectPreviewTab(name) {
    activePreviewTab = name;
    const previewActive = name === "preview";
    markdownPreview.classList.toggle("hidden", !previewActive);
    reader.classList.toggle("hidden", previewActive);
    previewTab.setAttribute("aria-selected", String(previewActive));
    speechPlanTab.setAttribute("aria-selected", String(!previewActive));
}

function showFileError(message) {
    fileError.textContent = message || "";
    fileError.classList.toggle("hidden", !message);
}

async function loadMarkdownFile(file) {
    const validation = MarkdownFile.validate(file);
    if (validation) return showFileError(validation);
    if (markdownInput.value && !confirm("Substituir o Markdown atual pelo conteúdo deste arquivo?")) return;
    try {
        const bytes = await file.arrayBuffer();
        const text = new TextDecoder("utf-8", {fatal: true}).decode(bytes);
        markdownInput.value = text;
        if (!filenameWasEdited || filenameInput.value === "narracao") {
            filenameInput.value = MarkdownFile.titleFromFilename(file.name);
        }
        showFileError("");
        updatePreview();
    } catch (error) {
        showFileError("Não foi possível ler o arquivo como UTF-8 válido.");
    } finally {
        markdownFileInput.value = "";
    }
}
function setProgress(progress, message) {
    const value = Math.min(100, Math.max(0, Number(progress) || 0));
    progressBar.style.width = `${value}%`;
    progressPercent.textContent = `${Math.round(value)}%`;
    progressMessage.textContent = message || "Processando...";
}
async function startGeneration() {
    const markdown = markdownInput.value.trim();
    if (!markdown) return alert("Cole um Markdown primeiro.");
    generateButton.disabled = true;
    generationCard.classList.remove("hidden"); playerDock.classList.add("hidden");
    setProgress(0, "Criando tarefa...");
    try {
        const response = await fetch("/api/generate", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({markdown, filename: filenameInput.value.trim() || "narracao"})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Falha ao iniciar.");
        activeJobId = data.job_id; await pollJob();
    } catch (error) {
        setProgress(0, error.message); progressMessage.classList.add("error-message"); generateButton.disabled = false;
    }
}
async function pollJob() {
    if (!activeJobId) return;
    try {
        const response = await fetch(`/api/jobs/${activeJobId}`);
        if (!response.ok) throw new Error("Não foi possível consultar a geração.");
        const job = await response.json(); setProgress(job.progress, job.message);
        if (job.status === "completed") {
            generationCard.classList.add("hidden"); generateButton.disabled = false;
            await loadGeneration(job.generation_id); await loadLibrary(); return;
        }
        if (["error", "failed", "cancelled"].includes(job.status)) throw new Error(job.error || job.message);
        pollTimer = setTimeout(pollJob, 500);
    } catch (error) {
        progressMessage.classList.add("error-message"); progressMessage.textContent = error.message; generateButton.disabled = false;
        if (regenerationBusy) {
            regenerationBusy = false;
            renderUnits(timeline, true);
        }
    }
}

async function loadLibrary() {
    try {
        const response = await fetch("/api/library");
        if (!response.ok) return;
        const documents = (await response.json()).documents || [];
        const options = ['<option value="">Abrir geração da biblioteca...</option>'];
        documents.forEach(document => document.generations.filter(item => item.status === "completed").forEach(item => {
            options.push(`<option value="${item.generation_id}">${escapeHtml(document.title)} · ${escapeHtml(item.created_at)}</option>`);
        }));
        generationSelect.innerHTML = options.join("");
    } catch (error) { console.error(error); }
}
async function loadGeneration(generationId) {
    const response = await fetch(`/api/generations/${generationId}`);
    if (!response.ok) throw new Error("Geração não encontrada.");
    const generation = await response.json();
    if (generation.status !== "completed" || !generation.metadata) throw new Error("Geração ainda não está concluída.");
    const documentResponse = await fetch(`/api/library/${generation.document_id}`);
    if (documentResponse.ok) {
        const savedDocument = await documentResponse.json();
        markdownInput.value = savedDocument.markdown; filenameInput.value = savedDocument.title;
        updateMarkdownPreview(savedDocument.markdown);
    }
    activeGenerationId = generationId; timeline = generation.metadata.timeline || []; activeUnitIndex = -1;
    regenerationAvailable = Boolean(generation.regeneration_available);
    regenerationBusy = false;
    renderUnits(timeline, true); downloadButton.href = generation.download_url;
    selectPreviewTab(activePreviewTab);
    playerDock.classList.remove("hidden");
    const playbackResponse = await fetch(`/api/generations/${generationId}/playback`);
    const saved = playbackResponse.ok ? await playbackResponse.json() : null;
    const defaultRate = Number(localStorage.getItem("markdownTtsSpeed") || 1);
    pendingPlayback = saved || {position_seconds: 0, playback_rate: defaultRate};
    if (!saved || saved.updated_at === null) pendingPlayback.playback_rate = defaultRate;
    audioPlayer.src = generation.audio_url;
    audioPlayer.load();
}

async function regenerateUnit(unitIdValue) {
    if (!regenerationAvailable || regenerationBusy || !activeGenerationId) return;
    regenerationBusy = true;
    document.querySelectorAll(".regenerate-button").forEach(button => { button.disabled = true; });
    generationCard.classList.remove("hidden");
    setProgress(0, `Enfileirando regeneração da unidade ${unitIdValue}...`);
    try {
        const response = await fetch(`/api/generations/${activeGenerationId}/units/${unitIdValue}/regenerate`, {method: "POST"});
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Regeneração indisponível.");
        activeJobId = data.job_id;
        await pollJob();
    } catch (error) {
        regenerationBusy = false;
        generationCard.classList.add("hidden");
        alert(error.message);
        renderUnits(timeline, true);
    }
}

function updateNavigationButtons() {
    previousButton.disabled = activeUnitIndex <= 0;
    nextButton.disabled = activeUnitIndex < 0 || activeUnitIndex >= timeline.length - 1;
    rewindButton.disabled = !Number.isFinite(audioPlayer.duration); forwardButton.disabled = !Number.isFinite(audioPlayer.duration);
}
function updateActiveUnit(currentTime, scroll = true) {
    const found = timelineIndexAt(currentTime);
    if (found === activeUnitIndex) return;
    document.querySelectorAll(".reader-unit.active").forEach(element => element.classList.remove("active"));
    activeUnitIndex = found; updateNavigationButtons();
    if (found < 0) return;
    const element = document.querySelector(`.reader-unit[data-index="${found}"]`);
    if (!element) return;
    element.classList.add("active");
    const focused = document.activeElement;
    const editing = focused && (focused.matches("textarea,input,select") || focused.isContentEditable);
    if (scroll && !editing) element.scrollIntoView({behavior: "smooth", block: "center"});
}
async function savePlayback(immediate = false) {
    if (!activeGenerationId) return;
    if (playbackSaveInFlight) {
        playbackSavePending = playbackSavePending || immediate;
        return;
    }
    const now = Date.now();
    if (!immediate && now - lastPlaybackSave < 3000) return;
    lastPlaybackSave = now; playbackSaveInFlight = true;
    const payload = {
        position_seconds: Math.max(0, Number(audioPlayer.currentTime) || 0),
        active_unit_id: activeUnitIndex >= 0 ? unitId(timeline[activeUnitIndex]) : null,
        playback_rate: Number(audioPlayer.playbackRate) || 1
    };
    try {
        await fetch(`/api/generations/${activeGenerationId}/playback`, {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload), keepalive: immediate});
    } catch (error) { console.error(error); }
    finally {
        playbackSaveInFlight = false;
        if (playbackSavePending) {
            playbackSavePending = false;
            savePlayback(true);
        }
    }
}
function moveUnit(direction) {
    if (activeUnitIndex < 0) return;
    let target = activeUnitIndex + direction;
    if (direction < 0) {
        const elapsed = audioPlayer.currentTime - Number(timeline[activeUnitIndex].start_seconds);
        target = elapsed > 3 ? activeUnitIndex : activeUnitIndex - 1;
    }
    if (target >= 0 && target < timeline.length) seekTo(Number(timeline[target].start_seconds));
}
function togglePlayback() {
    if (audioPlayer.paused) audioPlayer.play().catch(console.error); else audioPlayer.pause();
}

markdownInput.addEventListener("input", () => { clearTimeout(previewTimer); previewTimer = setTimeout(updatePreview, 350); });
filenameInput.addEventListener("input", () => { filenameWasEdited = true; });
previewTab.addEventListener("click", () => selectPreviewTab("preview"));
speechPlanTab.addEventListener("click", () => selectPreviewTab("speech"));
openMarkdownButton.addEventListener("click", () => markdownFileInput.click());
markdownDropZone.addEventListener("keydown", event => { if (event.target === markdownDropZone && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); markdownFileInput.click(); } });
markdownFileInput.addEventListener("change", () => { if (markdownFileInput.files[0]) loadMarkdownFile(markdownFileInput.files[0]); });
markdownDropZone.addEventListener("dragenter", event => { event.preventDefault(); dragDepth += 1; markdownDropZone.classList.add("drag-active"); });
markdownDropZone.addEventListener("dragover", event => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; });
markdownDropZone.addEventListener("dragleave", () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) markdownDropZone.classList.remove("drag-active"); });
markdownDropZone.addEventListener("drop", event => {
    event.preventDefault(); dragDepth = 0; markdownDropZone.classList.remove("drag-active");
    if (event.dataTransfer.files[0]) loadMarkdownFile(event.dataTransfer.files[0]);
});
generateButton.addEventListener("click", startGeneration);
generationSelect.addEventListener("change", () => { openGenerationButton.disabled = !generationSelect.value; });
openGenerationButton.addEventListener("click", () => loadGeneration(generationSelect.value).catch(error => alert(error.message)));
playButton.addEventListener("click", togglePlayback);
rewindButton.addEventListener("click", () => seekTo(audioPlayer.currentTime - 10));
forwardButton.addEventListener("click", () => seekTo(audioPlayer.currentTime + 10));
previousButton.addEventListener("click", () => moveUnit(-1)); nextButton.addEventListener("click", () => moveUnit(1));
audioPlayer.addEventListener("play", () => { playButton.textContent = "❚❚"; playButton.ariaLabel = "Pausar"; });
audioPlayer.addEventListener("pause", () => { playButton.textContent = "▶"; playButton.ariaLabel = "Reproduzir"; savePlayback(true); });
audioPlayer.addEventListener("loadedmetadata", () => {
    durationLabel.textContent = formatTime(audioPlayer.duration);
    if (pendingPlayback) {
        const initializeState = pendingPlayback.updated_at == null;
        const rate = Number(pendingPlayback.playback_rate) || 1;
        speedSelect.value = String(rate); audioPlayer.playbackRate = rate;
        seekTo(Math.min(Number(pendingPlayback.position_seconds) || 0, audioPlayer.duration), false); pendingPlayback = null;
        if (initializeState) savePlayback(true);
    }
    updateNavigationButtons();
});
seekBar.addEventListener("input", () => {
    if (Number.isFinite(audioPlayer.duration)) seekTo(Number(seekBar.value) / 1000 * audioPlayer.duration, false);
});
seekBar.addEventListener("change", () => savePlayback(true));
speedSelect.addEventListener("change", () => {
    const speed = Number(speedSelect.value); audioPlayer.playbackRate = speed;
    localStorage.setItem("markdownTtsSpeed", String(speed)); savePlayback(true);
});
audioPlayer.addEventListener("timeupdate", () => {
    currentTimeLabel.textContent = formatTime(audioPlayer.currentTime);
    if (Number.isFinite(audioPlayer.duration) && audioPlayer.duration > 0) seekBar.value = Math.round(audioPlayer.currentTime / audioPlayer.duration * 1000);
    updateActiveUnit(audioPlayer.currentTime); savePlayback(false);
});
document.addEventListener("keydown", event => {
    const target = event.target;
    if (target && (target.matches("textarea,input,select") || target.isContentEditable)) return;
    if (event.code === "Space") { event.preventDefault(); togglePlayback(); }
    else if (event.key === "ArrowLeft") { event.preventDefault(); event.ctrlKey || event.shiftKey ? moveUnit(-1) : seekTo(audioPlayer.currentTime - 10); }
    else if (event.key === "ArrowRight") { event.preventDefault(); event.ctrlKey || event.shiftKey ? moveUnit(1) : seekTo(audioPlayer.currentTime + 10); }
});
window.addEventListener("beforeunload", () => savePlayback(true));

loadLibrary(); updatePreview();
