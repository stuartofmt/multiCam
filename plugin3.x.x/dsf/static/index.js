async function loadCameras() {

    console.warn("Loading current camera UI script");

    const response = await fetch("/api/cameras");

    const data = await response.json();

    console.warn("Cameras received:", data.cameras);

    const grid = document.getElementById("camera-grid");

    grid.innerHTML = "";

    if (!data.cameras || data.cameras.length === 0) {
        const emptyState = document.createElement("div");
        emptyState.className = "camera-card empty-state";
        emptyState.textContent = "No cameras configured.";
        grid.appendChild(emptyState);
        return;
    }

    for (const camera of data.cameras) {

        const cameraName = camera.name;

        const card = document.createElement("div");
        card.className = "camera-card";

        const streamUrl = new URL(`/${cameraName}/${camera.stream}`, window.location.href);
        const snapshotUrl = new URL(`/${cameraName}/${camera.snapshot}`, window.location.href);
        const title = document.createElement("h2");
        title.innerText = cameraName;

        const streamLink = document.createElement("a");
        streamLink.href = streamUrl.href;
        streamLink.target = "_blank";
        streamLink.rel = "noopener noreferrer";
        streamLink.innerText = `Stream: ${streamUrl.href}`;

        const snapshotLink = document.createElement("a");
        snapshotLink.href = snapshotUrl.href;
        snapshotLink.target = "_blank";
        snapshotLink.rel = "noopener noreferrer";
        snapshotLink.innerText = `Snapshot: ${snapshotUrl.href}`;

        console.warn(`Camera: ${cameraName}, Stream URL: ${streamUrl.href}, Snapshot URL: ${snapshotUrl.href}`);
        const img = document.createElement("img");

        img.dataset.stream = streamUrl.href;
        img.src = uniqueStreamUrl(img.dataset.stream);
        img.alt = cameraName;

        card.appendChild(title);
        card.appendChild(streamLink);
        card.appendChild(document.createElement("br"));
        card.appendChild(snapshotLink);
        card.appendChild(img);

        grid.appendChild(card);
    }
}

// Browsers allow only ~6 connections per host, and each <img> stream holds one open.
// Release them while this tab is hidden so stream / snapshot links opened in other tabs can connect.
const BLANK_IMAGE = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";

// A stream response never completes, so browsers (notably WebKit / iOS) may hold or merge a second
// request for a URL already streaming in another tab. A unique query string forces a fresh connection.
function uniqueStreamUrl(stream) {
    const url = new URL(stream);
    url.searchParams.set("t", Date.now());
    return url.href;
}

document.addEventListener("visibilitychange", () => {
    const images = document.querySelectorAll("img[data-stream]");

    if (document.hidden) {
        // WebKit (all iOS browsers) keeps an MJPEG connection open when an <img> src changes;
        // window.stop() is what actually aborts the in-flight stream loads.
        window.stop();
        for (const img of images) {
            img.src = BLANK_IMAGE;
        }
        return;
    }

    if (images.length === 0) {
        // Hidden before the initial camera list finished loading (window.stop() aborted it).
        loadCameras();
        return;
    }

    for (const img of images) {
        img.src = uniqueStreamUrl(img.dataset.stream);
    }
});

loadCameras();