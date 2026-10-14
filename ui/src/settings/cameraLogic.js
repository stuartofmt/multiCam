// Lookups and formatting for the settings page.
// `settings` is the GET /api/settings response; requested values come from the settings file,
// Min / Max / Default and Effective from what the cameras reported when multiCam started.

export const ROTATIONS = ["0", "90", "180", "270"];

const TYPE_HINTS = {
    USB: "/dev/video0",
    PICAMERA: "0",
    STREAM: "http://host:port/path",
};

// "linux" or "windows" (from the server). On Windows a USB camera is a camera number (0, 1, ...), not /dev/videoN.
export function isWindows(settings) {
    return settings.schema.platform === "windows";
}

export function typeHint(settings, cameratype) {
    return cameratype === "USB" && isWindows(settings) ? "0" : TYPE_HINTS[cameratype] || "";
}

export function settingKeys(settings, cameratype) {
    return Object.keys(settings.schema.settings[cameratype] || {});
}

export function optionKeys(settings, cameratype) {
    return settings.schema.options[cameratype] || [];
}

export function keysFor(settings, cameratype) {
    return [...settingKeys(settings, cameratype), ...optionKeys(settings, cameratype)];
}

export function capabilitiesFor(settings, camera) {
    const devices = settings.capabilities[camera.cameratype];
    return devices ? devices[camera.source.trim()] : undefined;
}

// Effective values only apply while the camera is still the one that is running.
export function effectiveFor(settings, camera) {
    const loaded = camera.loaded;
    if (!loaded || loaded.cameratype !== camera.cameratype || loaded.source !== camera.source.trim()) {
        return null;
    }
    return settings.effective[loaded.name] || null;
}

export function detectedSources(settings, cameratype) {
    return Object.keys(settings.capabilities[cameratype] || {});
}

export function settingBounds(settings, camera, key) {
    const capabilities = capabilitiesFor(settings, camera);
    const device = (capabilities && capabilities.settings && capabilities.settings[key]) || {};
    const limits = settings.setting_limits[key] || {};
    return {
        min: device.min ?? limits.min,
        max: device.max ?? limits.max,
        default: settings.schema.settings[camera.cameratype][key],
    };
}

// Returns bounds, or a string explaining why there are none.
export function optionBounds(settings, camera, key) {
    const capabilities = capabilitiesFor(settings, camera);
    if (!capabilities) {
        return "camera not detected";
    }
    if (!capabilities.options) {
        // Windows without DirectShow access does not report control ranges: the value is passed to the camera as set
        return isWindows(settings) ? "not reported on Windows" : "unknown - camera was busy";
    }
    return capabilities.options[key] || "not supported by camera";
}

export function formatValue(value) {
    if (value === null || value === undefined || value === "") {
        return "–";
    }
    if (typeof value === "number" && !Number.isInteger(value)) {
        return String(Number(value.toFixed(3)));
    }
    return String(value);
}

export function sameValue(requested, effective) {
    const a = Number(requested);
    const b = Number(effective);
    if (String(requested).trim() !== "" && !Number.isNaN(a) && !Number.isNaN(b)) {
        return Math.abs(a - b) < 1e-6;
    }
    return String(requested).trim() === String(effective);
}

export function sourceFitsType(settings, source, cameratype) {
    source = source.trim();
    if (cameratype === "USB") {
        return isWindows(settings) ? /^\d+$/.test(source) : source.startsWith("/dev/video");
    }
    if (cameratype === "PICAMERA") {
        return /^\d+$/.test(source);
    }
    return /^(https?|rtsp):\/\//.test(source);
}

// First detected source of this type that no other camera uses.
export function unusedSource(settings, cameras, cameratype, except) {
    const used = new Set(
        cameras.filter((c) => c.id !== except?.id && c.cameratype === cameratype).map((c) => c.source.trim())
    );
    return detectedSources(settings, cameratype).find((source) => !used.has(source)) || "";
}

export function sortByName(cameras) {
    return [...cameras].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "variant" }));
}
