async function loadCameras() {

    const response = await fetch("/api/cameras");

    const data = await response.json();

    const grid = document.getElementById("camera-grid");

    grid.innerHTML = "";

    for (const cameraName of data.cameras) {

        const card = document.createElement("div");
        card.className = "camera-card";

        const title = document.createElement("h2");
        title.innerText = cameraName;

        const img = document.createElement("img");

        img.src = `/streaming/${cameraName}`;
        img.alt = cameraName;

        card.appendChild(title);
        card.appendChild(img);

        grid.appendChild(card);
    }
}

loadCameras();