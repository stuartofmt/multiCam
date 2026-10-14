// Calls to the multiCam server. Each returns the parsed JSON or throws an Error with a readable message.

async function request(url, options = {}) {
    const response = await fetch(url, { cache: "no-store", ...options });
    let data = {};
    try {
        data = await response.json();
    } catch {
        // Non-JSON error page - fall through to the status message.
    }
    if (!response.ok || (data.status && data.status !== "success")) {
        const error = new Error(data.message || `Request failed (${response.status})`);
        error.errors = data.errors;
        throw error;
    }
    return data;
}

export function getCameras() {
    return request("/api/cameras");
}

export function getSettings() {
    return request("/api/settings");
}

export function saveSettings(payload) {
    return request("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
}

export function restart() {
    return request("/api/restart", { method: "POST" });
}
