<script setup>
// Settings page: edits the cameras, port and log level kept in multiCam's settings file.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { mdiArrowLeft, mdiContentSave, mdiPlus, mdiUndo, mdiVideoOff } from "@mdi/js";
import { getSettings, saveSettings } from "../api.js";
import { keysFor, sortByName, unusedSource } from "./cameraLogic.js";
import CameraCard from "./CameraCard.vue";

// Server response from GET /api/settings
const settings = ref(null);
// Editable copies of the cameras. loaded holds the camera as read from the file (null if added here).
const cameras = ref([]);
const logLevel = ref("INFO");
const port = ref("0");
const loading = ref(true);
const saving = ref(false);
// { type: "error" | "success", lines: [...] }
const message = ref(null);
const messageBox = ref(null);
// What was last loaded, to tell whether anything has changed.
const savedState = ref("");
const cardRefs = new Map();
let nextId = 1;

const confirmDialog = ref({ show: false, title: "", text: "", action: null });

// Only INFO and DEBUG are offered, but a level already in the file (e.g. WARNING)
// is shown so saving doesn't change it unasked.
const logLevelItems = computed(() => {
    const items = ["INFO", "DEBUG"];
    if (logLevel.value && !items.includes(logLevel.value)) {
        items.push(logLevel.value);
    }
    return items;
});

const portInUse = computed(() => settings.value?.port_in_use);

function currentState() {
    return JSON.stringify({ cameras: cameras.value, logLevel: logLevel.value, port: String(port.value ?? "") });
}

const dirty = computed(() => settings.value !== null && currentState() !== savedState.value);

// Any edit clears the last save / error message. Sync, so that reloading after a save
// clears the old message before "Settings saved." is shown rather than after.
watch(currentState, () => {
    message.value = null;
}, { flush: "sync" });

function showMessage(type, lines) {
    message.value = { type, lines };
    nextTick(() => messageBox.value?.$el?.scrollIntoView({ behavior: "smooth", block: "nearest" }));
}

async function load() {
    loading.value = true;
    let data;
    try {
        data = await getSettings();
    } catch (e) {
        loading.value = false;
        showMessage("error", [`Could not load settings: ${e.message}`]);
        return false;
    }

    settings.value = data;
    cameras.value = sortByName(
        data.cameras.map((camera) => ({
            id: nextId++,
            name: camera.name,
            cameratype: camera.cameratype,
            source: camera.source,
            values: { ...camera.values },
            loaded: { name: camera.name, cameratype: camera.cameratype, source: camera.source },
        }))
    );
    logLevel.value = data.log_level;
    port.value = String(data.port);
    savedState.value = currentState();
    loading.value = false;
    return true;
}

async function save() {
    // A blank port means 0 - pick a free port.
    const portText = String(port.value ?? "").trim();
    const portNumber = portText === "" ? 0 : Number(portText);
    if (!Number.isInteger(portNumber)) {
        showMessage("error", ["Port must be a whole number (0 picks a free port)"]);
        return;
    }

    const payload = {
        log_level: logLevel.value,
        port: portNumber,
        cameras: cameras.value.map((camera) => {
            const keys = keysFor(settings.value, camera.cameratype);
            const values = {};
            for (const [key, value] of Object.entries(camera.values)) {
                if (keys.includes(key) && value !== null && value !== undefined && String(value).trim() !== "") {
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

    saving.value = true;
    try {
        await saveSettings(payload);
    } catch (e) {
        saving.value = false;
        showMessage("error", e.errors || [`Save failed: ${e.message}`]);
        return;
    }
    saving.value = false;

    if (await load()) {
        showMessage("success", ["Settings saved."]);
    }
}

function uniqueName() {
    const names = new Set(cameras.value.map((c) => c.name.trim()));
    let n = cameras.value.length + 1;
    while (names.has(`Camera${n}`)) {
        n++;
    }
    return `Camera${n}`;
}

async function addCamera() {
    if (!settings.value) {
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
    camera.source = unusedSource(settings.value, cameras.value, camera.cameratype, camera);
    cameras.value.push(camera);
    await nextTick();
    cardRefs.get(camera.id)?.focus();
}

function confirm(title, text, action) {
    confirmDialog.value = { show: true, title, text, action };
}

function runConfirmed() {
    const action = confirmDialog.value.action;
    confirmDialog.value.show = false;
    action?.();
}

function deleteCamera(camera) {
    const label = camera.name.trim() || "this camera";
    confirm(
        `Delete camera "${label}"?`,
        "The change is written to the settings file when you press Save.",
        () => {
            cameras.value = cameras.value.filter((c) => c.id !== camera.id);
        }
    );
}

function discardChanges() {
    confirm("Discard all unsaved changes?", "The settings are reloaded from the settings file.", async () => {
        await load();
        message.value = null;
    });
}

function setCardRef(id, component) {
    if (component) {
        cardRefs.set(id, component);
    } else {
        cardRefs.delete(id);
    }
}

function onBeforeUnload(event) {
    if (dirty.value) {
        event.preventDefault();
    }
}

onMounted(() => {
    window.addEventListener("beforeunload", onBeforeUnload);
    load();
});

onBeforeUnmount(() => {
    window.removeEventListener("beforeunload", onBeforeUnload);
});
</script>

<template>
    <v-app>
        <v-app-bar flat border="b">
            <v-app-bar-title>Camera Settings</v-app-bar-title>
            <template #append>
                <v-btn href="/" :prepend-icon="mdiArrowLeft" variant="tonal">Camera Streams</v-btn>
            </template>
        </v-app-bar>

        <v-main>
            <v-container fluid class="settings-page">
                <v-progress-linear v-if="loading" indeterminate color="primary" class="mb-4" />

                <p v-if="settings" class="text-medium-emphasis mb-4">Settings file: {{ settings.config_file }}</p>

                <v-alert v-if="settings?.restart_required" type="warning" variant="tonal" class="mb-4">
                    The settings have been saved. Restart multiCam (the plugin in DWC, or <code>sudo systemctl restart multicam</code> if installed standalone) for the changes to take effect.
                    The Effective column still shows the values the cameras are running with.
                </v-alert>

                <div aria-live="polite">
                    <v-alert
                        v-if="message"
                        ref="messageBox"
                        :type="message.type"
                        variant="tonal"
                        closable
                        class="mb-4"
                        @click:close="message = null"
                    >
                        <template v-if="message.lines.length === 1">{{ message.lines[0] }}</template>
                        <ul v-else class="ml-4">
                            <li v-for="line in message.lines" :key="line">{{ line }}</li>
                        </ul>
                    </v-alert>
                </div>

                <template v-if="settings">
                    <v-empty-state
                        v-if="cameras.length === 0"
                        :icon="mdiVideoOff"
                        title="No cameras configured"
                        text="Use “Add camera” to add one."
                    />

                    <div class="camera-list">
                        <CameraCard
                            v-for="camera in cameras"
                            :key="camera.id"
                            :ref="(component) => setCardRef(camera.id, component)"
                            :camera="camera"
                            :cameras="cameras"
                            :settings="settings"
                            @delete="deleteCamera"
                        />
                    </div>

                    <v-card class="mt-5">
                        <v-card-text>
                            <v-row dense align="center">
                                <v-col cols="12" md="auto" class="d-flex flex-wrap ga-2">
                                    <v-btn variant="tonal" :prepend-icon="mdiPlus" @click="addCamera">Add camera</v-btn>
                                    <v-btn
                                        color="primary"
                                        :prepend-icon="mdiContentSave"
                                        :disabled="!dirty"
                                        :loading="saving"
                                        @click="save"
                                    >
                                        Save
                                    </v-btn>
                                    <v-btn variant="tonal" :prepend-icon="mdiUndo" :disabled="!dirty" @click="discardChanges">
                                        Discard changes
                                    </v-btn>
                                </v-col>
                                <v-spacer />
                                <v-col cols="12" sm="6" md="3">
                                    <v-text-field
                                        v-model="port"
                                        label="Port"
                                        inputmode="numeric"
                                        :hint="`${portInUse ? `In use: ${portInUse}. ` : ''}0 picks a free port. Restart after changing.`"
                                        persistent-hint
                                    />
                                </v-col>
                                <v-col cols="12" sm="6" md="2">
                                    <v-select v-model="logLevel" :items="logLevelItems" label="Log level" hide-details />
                                </v-col>
                            </v-row>
                        </v-card-text>
                    </v-card>
                </template>
            </v-container>
        </v-main>

        <v-dialog v-model="confirmDialog.show" max-width="460">
            <v-card :title="confirmDialog.title" :text="confirmDialog.text">
                <v-card-actions>
                    <v-spacer />
                    <v-btn variant="text" @click="confirmDialog.show = false">Cancel</v-btn>
                    <v-btn color="primary" @click="runConfirmed">OK</v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>
    </v-app>
</template>

<style scoped>
.camera-list {
    display: flex;
    flex-direction: column;
    gap: 20px;
}
</style>
