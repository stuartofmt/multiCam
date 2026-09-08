async function loadCameras() {

    console.warn("Loading current camera UI script");

    const response = await fetch("/api/cameras");

    const data = await response.json();

    console.warn("Cameras received:", data.cameras);

    const grid = document.getElementById("camera-grid");

    grid.innerHTML = "";

    for (const cameraName of data.cameras) {

        const card = document.createElement("div");
        card.className = "camera-card";

        const title = document.createElement("h2");
        const streamUrl = new URL(`/${cameraName}/stream`, window.location.href);
        const snapshotUrl = new URL(`/${cameraName}/snapshot`, window.location.href);
        title.innerText = `${cameraName}\nStream: ${streamUrl.href}\nSnapshot: ${snapshotUrl.href}`;
        console.warn(`Camera: ${cameraName}, Stream URL: ${streamUrl.href}, Snapshot URL: ${snapshotUrl.href}`);
        const img = document.createElement("img");

        img.src = `/${cameraName}/stream`;
        img.alt = cameraName;

        card.appendChild(title);
        card.appendChild(img);

        grid.appendChild(card);
    }
}

loadCameras();