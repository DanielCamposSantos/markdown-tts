const markdownInput =
    document.getElementById(
        "markdownInput"
    );

const filenameInput =
    document.getElementById(
        "filename"
    );

const generateButton =
    document.getElementById(
        "generateButton"
    );

const reader =
    document.getElementById(
        "reader"
    );

const unitCount =
    document.getElementById(
        "unitCount"
    );

const generationCard =
    document.getElementById(
        "generationCard"
    );

const progressMessage =
    document.getElementById(
        "progressMessage"
    );

const progressPercent =
    document.getElementById(
        "progressPercent"
    );

const progressBar =
    document.getElementById(
        "progressBar"
    );

const playerDock =
    document.getElementById(
        "playerDock"
    );

const audioPlayer =
    document.getElementById(
        "audioPlayer"
    );

const playButton =
    document.getElementById(
        "playButton"
    );

const seekBar =
    document.getElementById(
        "seekBar"
    );

const currentTimeLabel =
    document.getElementById(
        "currentTime"
    );

const durationLabel =
    document.getElementById(
        "duration"
    );

const speedSelect =
    document.getElementById(
        "speedSelect"
    );

const downloadButton =
    document.getElementById(
        "downloadButton"
    );


let previewTimer = null;

let pollTimer = null;

let activeJobId = null;

let timeline = [];

let activeUnitIndex = -1;


function escapeHtml(
    value
) {
    return value
        .replaceAll(
            "&",
            "&amp;"
        )
        .replaceAll(
            "<",
            "&lt;"
        )
        .replaceAll(
            ">",
            "&gt;"
        )
        .replaceAll(
            "\"",
            "&quot;"
        )
        .replaceAll(
            "'",
            "&#039;"
        );
}


function formatTime(
    seconds
) {
    if (
        !Number.isFinite(
            seconds
        )
    ) {
        return "0:00";
    }

    const total =
        Math.max(
            0,
            Math.floor(seconds)
        );

    const minutes =
        Math.floor(
            total / 60
        );

    const remainder =
        total % 60;

    return (
        `${minutes}:` +
        `${remainder}`
            .padStart(
                2,
                "0"
            )
    );
}


function kindClass(
    kind
) {
    if (
        kind === "heading"
    ) {
        return "heading";
    }

    if (
        kind === "list"
    ) {
        return "list";
    }

    return "";
}


function renderUnits(
    units,
    useTimeline = false
) {
    if (
        !units
        || units.length === 0
    ) {
        reader.className =
            "reader empty-reader";

        reader.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">
                    ¶
                </div>

                <strong>
                    Aguardando Markdown
                </strong>

                <p>
                    As frases que serão narradas
                    aparecerão aqui.
                </p>
            </div>
        `;

        unitCount.textContent =
            "0 frases";

        return;
    }

    reader.className =
        "reader";

    unitCount.textContent =
        `${units.length} ` +
        (
            units.length === 1
                ? "unidade"
                : "unidades"
        );

    reader.innerHTML =
        units
            .map(
                (
                    unit,
                    index
                ) => {
                    const text =
                        unit.text
                        ?? unit.display_text
                        ?? "";

                    const start =
                        useTimeline
                            ? (
                                unit
                                .start_seconds
                            )
                            : null;

                    return `
                        <div
                            class="
                                reader-unit
                                ${kindClass(
                                    unit.kind
                                )}
                            "
                            data-index="${index}"
                            data-start="${
                                start ?? ""
                            }"
                        >${
                            escapeHtml(
                                text
                            )
                        }</div>
                    `;
                }
            )
            .join("");

    bindReaderClicks(
        useTimeline
    );
}


function bindReaderClicks(
    enabled
) {
    document
        .querySelectorAll(
            ".reader-unit"
        )
        .forEach(
            element => {
                element
                    .addEventListener(
                        "click",
                        () => {
                            if (
                                !enabled
                            ) {
                                return;
                            }

                            const start =
                                Number(
                                    element
                                    .dataset
                                    .start
                                );

                            if (
                                Number
                                    .isFinite(
                                        start
                                    )
                            ) {
                                audioPlayer
                                    .currentTime =
                                    start;

                                audioPlayer
                                    .play();
                            }
                        }
                    );
            }
        );
}


async function updatePreview() {
    const markdown =
        markdownInput
            .value
            .trim();

    if (!markdown) {
        renderUnits([]);
        return;
    }

    try {
        const response =
            await fetch(
                "/api/plan",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify(
                            {
                                markdown
                            }
                        )
                }
            );

        if (!response.ok) {
            throw new Error(
                "Preview inválido."
            );
        }

        const data =
            await response.json();

        renderUnits(
            data.units,
            false
        );

    } catch (
        error
    ) {
        console.error(
            error
        );
    }
}


markdownInput
    .addEventListener(
        "input",
        () => {
            clearTimeout(
                previewTimer
            );

            previewTimer =
                setTimeout(
                    updatePreview,
                    350
                );
        }
    );


function setProgress(
    progress,
    message
) {
    const value =
        Math.max(
            0,
            Math.min(
                100,
                Number(progress) || 0
            )
        );

    progressBar
        .style
        .width =
        `${value}%`;

    progressPercent
        .textContent =
        `${Math.round(value)}%`;

    progressMessage
        .textContent =
        message
        || "Processando...";
}


async function startGeneration() {
    const markdown =
        markdownInput
            .value
            .trim();

    if (!markdown) {
        alert(
            "Cole um Markdown primeiro."
        );

        return;
    }

    generateButton.disabled =
        true;

    generationCard
        .classList
        .remove(
            "hidden"
        );

    playerDock
        .classList
        .add(
            "hidden"
        );

    setProgress(
        0,
        "Criando tarefa..."
    );

    try {
        const response =
            await fetch(
                "/api/generate",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify(
                            {
                                markdown,

                                filename:
                                    filenameInput
                                        .value
                                        .trim()
                                    || "narracao"
                            }
                        )
                }
            );

        const data =
            await response
                .json();

        if (!response.ok) {
            throw new Error(
                data.detail
                || "Falha ao iniciar."
            );
        }

        activeJobId =
            data.job_id;

        await pollJob();

    } catch (
        error
    ) {
        setProgress(
            0,
            error.message
        );

        progressMessage
            .classList
            .add(
                "error-message"
            );

        generateButton.disabled =
            false;
    }
}


async function pollJob() {
    if (!activeJobId) {
        return;
    }

    try {
        const response =
            await fetch(
                `/api/jobs/${activeJobId}`
            );

        if (!response.ok) {
            throw new Error(
                "Não foi possível consultar a geração."
            );
        }

        const job =
            await response.json();

        setProgress(
            job.progress,
            job.message
        );

        if (
            job.status ===
            "completed"
        ) {
            finishGeneration(
                job
            );

            return;
        }

        if (
            job.status ===
            "error"
        ) {
            throw new Error(
                job.error
                || job.message
            );
        }

        pollTimer =
            setTimeout(
                pollJob,
                500
            );

    } catch (
        error
    ) {
        progressMessage
            .classList
            .add(
                "error-message"
            );

        progressMessage
            .textContent =
            error.message;

        generateButton.disabled =
            false;
    }
}


function finishGeneration(
    job
) {
    timeline =
        job.timeline || [];

    renderUnits(
        timeline,
        true
    );

    audioPlayer.src =
        job.audio_url;

    downloadButton.href =
        `/api/download/${activeJobId}`;

    speedSelect.value =
        localStorage
            .getItem(
                "markdownTtsSpeed"
            )
        || "1";

    audioPlayer
        .playbackRate =
        Number(
            speedSelect.value
        );

    generationCard
        .classList
        .add(
            "hidden"
        );

    playerDock
        .classList
        .remove(
            "hidden"
        );

    generateButton.disabled =
        false;

    activeUnitIndex =
        -1;
}


generateButton
    .addEventListener(
        "click",
        startGeneration
    );


playButton
    .addEventListener(
        "click",
        async () => {
            if (
                audioPlayer.paused
            ) {
                await audioPlayer
                    .play();
            } else {
                audioPlayer
                    .pause();
            }
        }
    );


audioPlayer
    .addEventListener(
        "play",
        () => {
            playButton.textContent =
                "❚❚";
        }
    );


audioPlayer
    .addEventListener(
        "pause",
        () => {
            playButton.textContent =
                "▶";
        }
    );


audioPlayer
    .addEventListener(
        "loadedmetadata",
        () => {
            durationLabel
                .textContent =
                formatTime(
                    audioPlayer
                        .duration
                );
        }
    );


seekBar
    .addEventListener(
        "input",
        () => {
            if (
                !Number.isFinite(
                    audioPlayer
                        .duration
                )
            ) {
                return;
            }

            audioPlayer
                .currentTime =
                (
                    Number(
                        seekBar.value
                    )
                    / 1000
                )
                * audioPlayer
                    .duration;
        }
    );


speedSelect
    .addEventListener(
        "change",
        () => {
            const speed =
                Number(
                    speedSelect.value
                );

            audioPlayer
                .playbackRate =
                speed;

            localStorage
                .setItem(
                    "markdownTtsSpeed",
                    String(speed)
                );
        }
    );


function updateActiveUnit(
    currentTime
) {
    if (
        !timeline
        || timeline.length === 0
    ) {
        return;
    }

    let found = -1;

    for (
        let i = 0;
        i < timeline.length;
        i++
    ) {
        const unit =
            timeline[i];

        if (
            currentTime
                >= unit.start_seconds
            &&
            currentTime
                <= (
                    unit.end_seconds
                    + (
                        unit
                        .pause_after_ms
                        / 1000
                    )
                )
        ) {
            found = i;
            break;
        }
    }

    if (
        found ===
        activeUnitIndex
    ) {
        return;
    }

    document
        .querySelectorAll(
            ".reader-unit.active"
        )
        .forEach(
            element =>
                element
                    .classList
                    .remove(
                        "active"
                    )
        );

    activeUnitIndex =
        found;

    if (
        found < 0
    ) {
        return;
    }

    const element =
        document
            .querySelector(
                `.reader-unit[data-index="${found}"]`
            );

    if (!element) {
        return;
    }

    element
        .classList
        .add(
            "active"
        );

    element
        .scrollIntoView(
            {
                behavior:
                    "smooth",

                block:
                    "center"
            }
        );
}


audioPlayer
    .addEventListener(
        "timeupdate",
        () => {
            currentTimeLabel
                .textContent =
                formatTime(
                    audioPlayer
                        .currentTime
                );

            if (
                Number.isFinite(
                    audioPlayer
                        .duration
                )
                &&
                audioPlayer.duration > 0
            ) {
                seekBar.value =
                    Math.round(
                        (
                            audioPlayer
                                .currentTime
                            /
                            audioPlayer
                                .duration
                        )
                        * 1000
                    );
            }

            updateActiveUnit(
                audioPlayer
                    .currentTime
            );
        }
    );


const savedSpeed =
    localStorage
        .getItem(
            "markdownTtsSpeed"
        );

if (savedSpeed) {
    speedSelect.value =
        savedSpeed;
}


updatePreview();