// Settings page: edits the camera entries of multiCam.config.
// Requested values come from the config file, Min / Max / Default and Effective
// from what the cameras reported when multiCam started.

const ROTATIONS = ["0", "90", "180", "270"];

const TYPE_HINTS = {
    USB: "/dev/video0",
    PICAMERA: "0",
    STREAM: "http://host:port/path",
};

// Server response from GET /api/settings
let settings = null;
// Editable copies of the cameras. loaded holds the camera as read from the file (null if added here).
let cameras = [];
let dirty = false;
let nextId = 1;

const cameraList = document.getElementById("camera-list");
const messages = document.getElementById("messages");
const saveButton = document.getElementById("save");
const discardButton = document.getElementById("discard");
const logLevelSelect = document.getElementById("log-level");

// ------------------------------------------------------------
// Loading and saving
// ------------------------------------------------------------

async function loadSettings() {
    let data;
    try {
        const response = await fetch("/api/settings", { cache: "no-store" });
        data = await response.json();
        if (!response.ok || data.status !== "success") {
            throw new Error(data.message || `Request failed (${response.status})`);
        }
    } catch (e) {
        showMessages("error", [`Could not load settings: ${e.message}`]);
        return;
    }

    settings = data;
    cameras = data.cameras.map((camera) => ({
        id: nextId++,
        name: camera.name,
        cameratype: camera.cameratype,
        source: camera.source,
        values: { ...camera.values },
        loaded: { name: camera.name, cameratype: camera.cameratype, source: camera.source },
    }));

    document.getElementById("config-file").textContent = `Configuration file: ${data.config_file}`;
    document.getElementById("restart-banner").hidden = !data.restart_required;
    renderLogLevel(data.log_level);
    setDirty(false);
    renderAll();
}

async function saveSettings() {
    const payload = {
        log_level: logLevelSelect.value,
        cameras: cameras.map((camera) => {
            const keys = keysFor(camera.cameratype);
            const values = {};
            for (const [key, value] of Object.entries(camera.values)) {
                if (keys.includes(key) && String(value).trim() !== "") {
                    values[key] = String(value).trim();
                }
            }
            return {
                name: camera.name.trim(),
                cameratype: camera.cameratype,
                source: camera.source.trim(),
                values,
            };
        }),
    };

    saveButton.disabled = true;
    let data;
    try {
        const response = await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        data = await response.json();
        if (!response.ok || data.status !== "success") {
            showMessages("error", data.errors || [data.message || `Save failed (${response.status})`]);
            saveButton.disabled = false;
            return;
        }
    } catch (e) {
        showMessages("error", [`Save failed: ${e.message}`]);
        saveButton.disabled = false;
        return;
    }

    await loadSettings();
    showMessages("success", ["Settings saved."]);
}

// ------------------------------------------------------------
// Lookups
// ------------------------------------------------------------

function settingKeys(cameratype) {
    return Object.keys(settings.schema.settings[cameratype] || {});
}

function optionKeys(cameratype) {
    return settings.schema.options[cameratype] || [];
}

function keysFor(cameratype) {
    return [...settingKeys(cameratype), ...optionKeys(cameratype)];
}

function capabilitiesFor(camera) {
    const devices = settings.capabilities[camera.cameratype];
    return devices ? devices[camera.source.trim()] : undefined;
}

// Effective values only apply while the camera is still the one that is running.
function effectiveFor(camera) {
    const loaded = camera.loaded;
    if (!loaded || loaded.cameratype !== camera.cameratype || loaded.source !== camera.source.trim()) {
        return null;
    }
    return settings.effective[loaded.name] || null;
}

function detectedSources(cameratype) {
    return Object.keys(settings.capabilities[cameratype] || {});
}

function settingBounds(camera, key) {
    const capabilities = capabilitiesFor(camera);
    const device = (capabilities && capabilities.settings && capabilities.settings[key]) || {};
    const limits = settings.setting_limits[key] || {};
    return {
        min: device.min ?? limits.min,
        max: device.max ?? limits.max,
        default: settings.schema.settings[camera.cameratype][key],
    };
}

// Returns bounds, or a string explaining why there are none.
function optionBounds(camera, key) {
    const capabilities = capabilitiesFor(camera);
    if (!capabilities) {
        return "camera not detected";
    }
    if (!capabilities.options) {
        return "unknown - camera was busy";
    }
    return capabilities.options[key] || "not supported by camera";
}

// ------------------------------------------------------------
// Formatting
// ------------------------------------------------------------

function formatValue(value) {
    if (value === null || value === undefined || value === "") {
        return "–";
    }
    if (typeof value === "number" && !Number.isInteger(value)) {
        return String(Number(value.toFixed(3)));
    }
    return String(value);
}

function sameValue(requested, effective) {
    const a = Number(requested);
    const b = Number(effective);
    if (String(requested).trim() !== "" && !Number.isNaN(a) && !Number.isNaN(b)) {
        return Math.abs(a - b) < 1e-6;
    }
    return String(requested).trim() === String(effective);
}

// ------------------------------------------------------------
// Rendering
// ------------------------------------------------------------

function renderAll() {
    cameraList.innerHTML = "";
    if (cameras.length === 0) {
        renderEmptyState();
        return;
    }
    const sorted = [...cameras].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "variant" }));
    for (const camera of sorted) {
        cameraList.appendChild(renderCard(camera));
    }
}

function renderEmptyState() {
    const empty = document.createElement("div");
    empty.className = "camera-card empty-state";
    empty.textContent = "No cameras configured. Use “Add camera” to add one.";
    cameraList.appendChild(empty);
}

function renderCard(camera) {
    const template = document.getElementById("camera-card-template");
    const card = template.content.firstElementChild.cloneNode(true);
    card.dataset.id = camera.id;

    const nameInput = card.querySelector(".camera-name");
    nameInput.value = camera.name;
    nameInput.addEventListener("input", () => {
        camera.name = nameInput.value;
        setDirty(true);
        refreshAllSourceOptions();
    });

    const typeSelect = card.querySelector(".camera-type");
    for (const cameratype of settings.schema.types) {
        typeSelect.add(new Option(cameratype, cameratype));
    }
    typeSelect.value = camera.cameratype;
    typeSelect.addEventListener("change", () => {
        camera.cameratype = typeSelect.value;
        if (!sourceFitsType(camera.source, camera.cameratype)) {
            camera.source = unusedSource(camera.cameratype, camera);
            sourceInput.value = camera.source;
        }
        setDirty(true);
        refreshAllSourceOptions();
        renderTable(card, camera);
    });

    const sourceInput = card.querySelector(".camera-source");
    const datalist = card.querySelector("datalist");
    datalist.id = `sources-${camera.id}`;
    sourceInput.setAttribute("list", datalist.id);
    sourceInput.value = camera.source;
    sourceInput.addEventListener("input", () => {
        camera.source = sourceInput.value;
        setDirty(true);
        renderTable(card, camera);
    });

    const sourceSelect = card.querySelector(".camera-source-select");
    sourceSelect.addEventListener("change", () => {
        camera.source = sourceSelect.value;
        sourceInput.value = camera.source;
        setDirty(true);
        refreshAllSourceOptions();
        renderTable(card, camera);
    });

    card.querySelector(".delete-camera").addEventListener("click", () => {
        const label = camera.name.trim() || "this camera";
        if (!confirm(`Delete camera "${label}"?\n\nThe change is written to the configuration file when you press Save.`)) {
            return;
        }
        cameras = cameras.filter((c) => c !== camera);
        card.remove();
        setDirty(true);
        refreshAllSourceOptions();
        if (cameras.length === 0) {
            renderEmptyState();
        }
    });

    renderSourceOptions(card, camera);
    renderTable(card, camera);
    return card;
}

function sourceFitsType(source, cameratype) {
    source = source.trim();
    if (cameratype === "USB") {
        return source.startsWith("/dev/video");
    }
    if (cameratype === "PICAMERA") {
        return /^\d+$/.test(source);
    }
    return /^(https?|rtsp):\/\//.test(source);
}

// First detected source of this type that no other camera uses.
function unusedSource(cameratype, except) {
    const used = new Set(
        cameras.filter((c) => c !== except && c.cameratype === cameratype).map((c) => c.source.trim())
    );
    return detectedSources(cameratype).find((source) => !used.has(source)) || "";
}

// Other cards' dropdowns show which sources are taken, so refresh them all.
function refreshAllSourceOptions() {
    for (const camera of cameras) {
        const card = cameraList.querySelector(`.camera-card[data-id="${camera.id}"]`);
        if (card) {
            renderSourceOptions(card, camera);
        }
    }
}

// USB and Pi cameras pick from the detected sources; streams take a free-text URL.
function renderSourceOptions(card, camera) {
    const sourceInput = card.querySelector(".camera-source");
    const sourceSelect = card.querySelector(".camera-source-select");
    const datalist = card.querySelector("datalist");
    const detected = detectedSources(camera.cameratype);
    const useSelect = camera.cameratype === "USB" || camera.cameratype === "PICAMERA";

    sourceSelect.hidden = !useSelect;
    sourceInput.hidden = useSelect;

    datalist.innerHTML = "";
    sourceSelect.innerHTML = "";
    if (!useSelect) {
        for (const source of detected) {
            datalist.appendChild(new Option(source, source));
        }
        sourceInput.placeholder = TYPE_HINTS[camera.cameratype] || "";
        return;
    }

    const current = camera.source.trim();
    if (current === "") {
        sourceSelect.add(new Option("-- select source --", ""));
    } else if (!detected.includes(current)) {
        sourceSelect.add(new Option(`${current} (not detected)`, current));
    }
    for (const source of detected) {
        const usedBy = cameras.find(
            (c) => c !== camera && c.cameratype === camera.cameratype && c.source.trim() === source
        );
        const label = usedBy ? `${source} (used by ${usedBy.name.trim() || "another camera"})` : source;
        sourceSelect.add(new Option(label, source));
    }
    if (detected.length === 0 && current === "") {
        sourceSelect.options[0].textContent = "-- no cameras detected --";
    }
    sourceSelect.value = current;
}

function renderStatus(card, camera, effective) {
    const status = card.querySelector(".status");
    if (!camera.loaded) {
        status.textContent = "New";
        status.className = "status status-new";
    } else if (effective) {
        status.textContent = "Running";
        status.className = "status status-running";
    } else if (camera.loaded.cameratype !== camera.cameratype || camera.loaded.source !== camera.source.trim()) {
        status.textContent = "Changed";
        status.className = "status status-new";
    } else {
        status.textContent = "Not running";
        status.className = "status status-stopped";
    }
}

function renderTable(card, camera) {
    const tbody = card.querySelector("tbody");
    tbody.innerHTML = "";
    const effective = effectiveFor(camera);
    renderStatus(card, camera, effective);

    const shownKeys = new Set();

    addGroupRow(tbody, "Settings");
    for (const key of settingKeys(camera.cameratype)) {
        const bounds = settingBounds(camera, key);
        addRow(tbody, camera, key, bounds, effective, `default (${bounds.default})`);
        shownKeys.add(key);
    }

    const options = optionKeys(camera.cameratype);
    if (options.length > 0) {
        addGroupRow(tbody, "Camera controls");
        for (const key of options) {
            const bounds = optionBounds(camera, key);
            const placeholder = typeof bounds === "object" ? `camera default (${formatValue(bounds.default)})` : "camera default";
            addRow(tbody, camera, key, bounds, effective, placeholder);
            shownKeys.add(key);
        }
    }

    // Values the camera was adjusted to that can't be set in the config file (e.g. format).
    const reported = effective ? Object.keys(effective).filter((key) => !shownKeys.has(key)) : [];
    if (reported.length > 0) {
        addGroupRow(tbody, "Reported by camera");
        for (const key of reported) {
            const row = tbody.insertRow();
            row.className = "readonly";
            addHeaderCell(row, key);
            row.insertCell().textContent = "–";
            row.insertCell().textContent = "–";
            row.insertCell().textContent = formatValue(effective[key]);
        }
    }
}

function addGroupRow(tbody, title) {
    const row = tbody.insertRow();
    row.className = "group";
    const cell = document.createElement("th");
    cell.colSpan = 4;
    cell.scope = "colgroup";
    cell.textContent = title;
    row.appendChild(cell);
}

function addHeaderCell(row, text) {
    const cell = document.createElement("th");
    cell.scope = "row";
    cell.textContent = text;
    row.appendChild(cell);
}

function addRow(tbody, camera, key, bounds, effective, placeholder) {
    const row = tbody.insertRow();
    addHeaderCell(row, key);

    const requestedCell = row.insertCell();
    const input = key === "rotate" ? rotateSelect(camera.values[key], placeholder) : document.createElement("input");
    if (key !== "rotate") {
        input.type = "text";
        input.value = camera.values[key] ?? "";
        input.placeholder = placeholder;
        if (typeof bounds !== "object" || typeof bounds.default === "number") {
            input.inputMode = "decimal";
        }
    }
    input.className = "requested";
    input.setAttribute("aria-label", `${camera.name} ${key} requested`);
    requestedCell.appendChild(input);

    const boundsCell = row.insertCell();
    if (typeof bounds === "string") {
        boundsCell.textContent = bounds;
        boundsCell.className = "muted";
    } else {
        boundsCell.textContent = [bounds.min, bounds.max, bounds.default].map(formatValue).join(" / ");
    }

    const effectiveCell = row.insertCell();
    const effectiveValue = effective ? effective[key] : undefined;
    effectiveCell.textContent = effective ? formatValue(effectiveValue) : "–";
    if (!effective) {
        effectiveCell.className = "muted";
    }

    // Highlight values the camera could not use as requested (e.g. clamped or unsupported).
    const markAdjusted = () => {
        const adjusted = effectiveValue !== undefined && input.value.trim() !== "" && !sameValue(input.value, effectiveValue);
        effectiveCell.classList.toggle("adjusted", adjusted);
        effectiveCell.title = adjusted ? "Differs from the requested value" : "";
    };
    markAdjusted();

    input.addEventListener(key === "rotate" ? "change" : "input", () => {
        camera.values[key] = input.value;
        setDirty(true);
        markAdjusted();
    });
}

function rotateSelect(value, placeholder) {
    const select = document.createElement("select");
    select.add(new Option(placeholder, ""));
    const choices = [...ROTATIONS];
    if (value && !choices.includes(String(value))) {
        choices.push(String(value));
    }
    for (const rotation of choices) {
        select.add(new Option(rotation, rotation));
    }
    select.value = value ?? "";
    return select;
}

// Only INFO and DEBUG are offered, but a level already in the file (e.g. WARNING)
// is shown so saving doesn't change it unasked.
function renderLogLevel(level) {
    for (const option of [...logLevelSelect.options]) {
        if (option.dataset.fromFile) {
            option.remove();
        }
    }
    if (![...logLevelSelect.options].some((option) => option.value === level)) {
        const option = new Option(level, level);
        option.dataset.fromFile = "true";
        logLevelSelect.add(option);
    }
    logLevelSelect.value = level;
}

// ------------------------------------------------------------
// Page state
// ------------------------------------------------------------

function setDirty(value) {
    dirty = value;
    saveButton.disabled = !dirty;
    discardButton.disabled = !dirty;
    if (dirty) {
        messages.innerHTML = "";
    }
}

function showMessages(kind, lines) {
    messages.innerHTML = "";
    const box = document.createElement("div");
    box.className = `banner banner-${kind}`;
    if (lines.length === 1) {
        box.textContent = lines[0];
    } else {
        const list = document.createElement("ul");
        for (const line of lines) {
            const item = document.createElement("li");
            item.textContent = line;
            list.appendChild(item);
        }
        box.appendChild(list);
    }
    messages.appendChild(box);
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function uniqueName() {
    const names = new Set(cameras.map((c) => c.name.trim()));
    let n = cameras.length + 1;
    while (names.has(`Camera${n}`)) {
        n++;
    }
    return `Camera${n}`;
}

document.getElementById("add-camera").addEventListener("click", () => {
    if (!settings) {
        return;
    }
    const camera = {
        id: nextId++,
        name: uniqueName(),
        cameratype: "USB",
        source: "",
        values: {},
        loaded: null,
    };
    camera.source = unusedSource(camera.cameratype, camera);
    if (cameras.length === 0) {
        cameraList.innerHTML = "";
    }
    cameras.push(camera);
    const card = renderCard(camera);
    cameraList.appendChild(card);
    setDirty(true);
    refreshAllSourceOptions();
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    card.querySelector(".camera-name").focus({ preventScroll: true });
});

saveButton.addEventListener("click", saveSettings);

logLevelSelect.addEventListener("change", () => setDirty(true));

discardButton.addEventListener("click", () => {
    if (confirm("Discard all unsaved changes?")) {
        messages.innerHTML = "";
        loadSettings();
    }
});

window.addEventListener("beforeunload", (event) => {
    if (dirty) {
        event.preventDefault();
    }
});

loadSettings();
