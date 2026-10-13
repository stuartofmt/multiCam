<script setup>
// Camera streams page: shows every running camera's MJPEG stream with its stream and snapshot links.
import { onBeforeUnmount, onMounted, ref } from "vue";
import { mdiCog, mdiVideoOff } from "@mdi/js";
import { getCameras } from "../api.js";

// Browsers allow only ~6 connections per host, and each <img> stream holds one open.
// The streams are released while this tab is hidden so links opened in other tabs can connect.
const BLANK_IMAGE = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";

const cameras = ref([]);
const loading = ref(true);
const error = ref("");
const paused = ref(false);
// Changed whenever the streams are reconnected - see streamSrc.
const connection = ref(Date.now());

async function loadCameras() {
    loading.value = true;
    error.value = "";
    try {
        const data = await getCameras();
        cameras.value = (data.cameras || []).map((camera) => ({
            name: camera.name,
            streamUrl: cameraUrl(camera.name, camera.stream),
            snapshotUrl: cameraUrl(camera.name, camera.snapshot),
        }));
    } catch (e) {
        error.value = `Could not load cameras: ${e.message}`;
    } finally {
        loading.value = false;
    }
}

function cameraUrl(name, endpoint) {
    return new URL(`/${encodeURIComponent(name)}/${encodeURIComponent(endpoint)}`, window.location.href).href;
}

// A stream response never completes, so browsers (notably WebKit / iOS) may hold or merge a second
// request for a URL already streaming in another tab. A unique query string forces a fresh connection.
function streamSrc(camera) {
    if (paused.value) {
        return BLANK_IMAGE;
    }
    const url = new URL(camera.streamUrl);
    url.searchParams.set("t", connection.value);
    return url.href;
}

function onVisibilityChange() {
    if (document.hidden) {
        // WebKit (all iOS browsers) keeps an MJPEG connection open when an <img> src changes;
        // window.stop() is what actually aborts the in-flight stream loads.
        window.stop();
        paused.value = true;
        return;
    }
    paused.value = false;
    connection.value = Date.now();
    if (cameras.value.length === 0) {
        // Hidden before the camera list finished loading (window.stop() aborted it).
        loadCameras();
    }
}

onMounted(() => {
    document.addEventListener("visibilitychange", onVisibilityChange);
    loadCameras();
});

onBeforeUnmount(() => {
    document.removeEventListener("visibilitychange", onVisibilityChange);
});
</script>

<template>
    <v-app>
        <v-app-bar flat border="b">
            <v-app-bar-title>Camera Streams</v-app-bar-title>
            <template #append>
                <v-btn href="/settings" :prepend-icon="mdiCog" variant="tonal">Settings</v-btn>
            </template>
        </v-app-bar>

        <v-main>
            <v-container fluid>
                <v-progress-linear v-if="loading" indeterminate color="primary" class="mb-4" />

                <v-alert v-if="error" type="error" variant="tonal" class="mb-4">
                    {{ error }}
                    <template #append>
                        <v-btn variant="text" @click="loadCameras">Retry</v-btn>
                    </template>
                </v-alert>

                <v-empty-state
                    v-if="!loading && !error && cameras.length === 0"
                    :icon="mdiVideoOff"
                    title="No cameras configured"
                    text="Add cameras on the settings page."
                    action-text="Open settings"
                    href="/settings"
                />

                <div v-else class="camera-grid" aria-live="polite">
                    <v-card v-for="camera in cameras" :key="camera.name" class="camera-card">
                        <v-card-title class="camera-title">{{ camera.name }}</v-card-title>
                        <v-card-text class="pb-2">
                            <div class="camera-link">
                                Stream:
                                <a :href="camera.streamUrl" target="_blank" rel="noopener noreferrer">{{ camera.streamUrl }}</a>
                            </div>
                            <div class="camera-link">
                                Snapshot:
                                <a :href="camera.snapshotUrl" target="_blank" rel="noopener noreferrer">{{ camera.snapshotUrl }}</a>
                            </div>
                        </v-card-text>
                        <img class="camera-image" :src="streamSrc(camera)" :alt="camera.name">
                    </v-card>
                </div>
            </v-container>
        </v-main>
    </v-app>
</template>

<style scoped>
.camera-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(400px, 100%), 1fr));
    gap: 20px;
}

.camera-card {
    padding-bottom: 12px;
}

.camera-title {
    white-space: pre-line;
    overflow-wrap: anywhere;
}

.camera-link {
    overflow-wrap: anywhere;
}

.camera-link a {
    color: inherit;
}

.camera-image {
    display: block;
    width: calc(100% - 24px);
    height: auto;
    margin: 0 12px;
    border-radius: 8px;
    background: black;
}
</style>
