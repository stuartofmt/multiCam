<script setup>
// One camera on the settings page: name, type, source and the table of settings / camera controls.
// The camera object belongs to SettingsApp and is edited in place.
import { computed, ref } from "vue";
import { mdiDelete } from "@mdi/js";
import {
    ROTATIONS,
    detectedSources,
    effectiveFor,
    formatValue,
    optionBounds,
    optionKeys,
    sameValue,
    settingBounds,
    settingKeys,
    sourceFitsType,
    typeHint,
    unusedSource,
} from "./cameraLogic.js";

const props = defineProps({
    camera: { type: Object, required: true },
    // All cameras on the page, to show which sources are already used.
    cameras: { type: Array, required: true },
    // GET /api/settings response
    settings: { type: Object, required: true },
});

const emit = defineEmits(["delete"]);

const card = ref(null);
const nameField = ref(null);

defineExpose({
    // Scroll a newly added camera into view and start editing its name.
    focus() {
        card.value?.$el.scrollIntoView({ behavior: "smooth", block: "start" });
        nameField.value?.focus();
    },
});

const camera = computed(() => props.camera);
const effective = computed(() => effectiveFor(props.settings, props.camera));

// USB and Pi cameras pick from the detected sources; streams take a free-text URL.
const usesSourceSelect = computed(() => ["USB", "PICAMERA"].includes(props.camera.cameratype));

const sourceItems = computed(() => {
    const detected = detectedSources(props.settings, props.camera.cameratype);
    const current = props.camera.source.trim();
    const items = [];
    if (current !== "" && !detected.includes(current)) {
        items.push({ title: `${current} (not detected)`, value: current });
    }
    for (const source of detected) {
        const usedBy = props.cameras.find(
            (c) => c.id !== props.camera.id && c.cameratype === props.camera.cameratype && c.source.trim() === source
        );
        // Windows: the camera's name next to its number
        const name = props.settings.capabilities[props.camera.cameratype]?.[source]?.name;
        const label = name ? `${source} - ${name}` : source;
        items.push({
            title: usedBy ? `${label} (used by ${usedBy.name.trim() || "another camera"})` : label,
            value: source,
        });
    }
    return items;
});

const sourcePlaceholder = computed(() => {
    if (!usesSourceSelect.value) {
        return typeHint(props.settings, props.camera.cameratype);
    }
    return detectedSources(props.settings, props.camera.cameratype).length ? "Select source" : "No cameras detected";
});

function setType(cameratype) {
    props.camera.cameratype = cameratype;
    if (!sourceFitsType(props.settings, props.camera.source, cameratype)) {
        props.camera.source = unusedSource(props.settings, props.cameras, cameratype, props.camera);
    }
}

const status = computed(() => {
    const loaded = props.camera.loaded;
    if (!loaded) {
        return { text: "New", color: "info" };
    }
    if (effective.value) {
        return { text: "Running", color: "success" };
    }
    if (loaded.cameratype !== props.camera.cameratype || loaded.source !== props.camera.source.trim()) {
        return { text: "Changed", color: "info" };
    }
    return { text: "Not running", color: "error" };
});

// Table rows: group headings, editable settings / controls, and values only reported by the camera.
const rows = computed(() => {
    const { settings, camera: cam } = props;
    const rows = [];
    const shownKeys = new Set();

    rows.push({ kind: "group", title: "Settings" });
    for (const key of settingKeys(settings, cam.cameratype)) {
        const bounds = settingBounds(settings, cam, key);
        rows.push({
            kind: "edit",
            key,
            bounds,
            placeholder: `default (${bounds.default})`,
            numeric: typeof bounds.default === "number",
        });
        shownKeys.add(key);
    }

    const options = optionKeys(settings, cam.cameratype);
    if (options.length > 0) {
        rows.push({ kind: "group", title: "Camera controls" });
        for (const key of options) {
            const bounds = optionBounds(settings, cam, key);
            rows.push({
                kind: "edit",
                key,
                bounds,
                placeholder: typeof bounds === "object" ? `camera default (${formatValue(bounds.default)})` : "camera default",
                numeric: true,
            });
            shownKeys.add(key);
        }
    }

    // Values the camera was adjusted to that can't be set in the settings file (e.g. format).
    const reported = effective.value ? Object.keys(effective.value).filter((key) => !shownKeys.has(key)) : [];
    if (reported.length > 0) {
        rows.push({ kind: "group", title: "Reported by camera" });
        for (const key of reported) {
            rows.push({ kind: "reported", key, value: effective.value[key] });
        }
    }
    return rows;
});

function boundsText(bounds) {
    return typeof bounds === "string" ? bounds : [bounds.min, bounds.max, bounds.default].map(formatValue).join(" / ");
}

function effectiveText(key) {
    return effective.value ? formatValue(effective.value[key]) : "–";
}

// Values the camera could not use as requested (e.g. clamped or unsupported) are highlighted.
function isAdjusted(key) {
    const value = effective.value ? effective.value[key] : undefined;
    const requested = String(props.camera.values[key] ?? "").trim();
    return value !== undefined && requested !== "" && !sameValue(requested, value);
}

function rotateItems() {
    const current = props.camera.values.rotate;
    const items = [...ROTATIONS];
    if (current && !items.includes(String(current))) {
        items.push(String(current));
    }
    return items;
}
</script>

<template>
    <v-card ref="card" class="camera-card" :data-camera-id="camera.id">
        <v-card-text>
            <v-row dense align="center">
                <v-col cols="12" sm="6" md="3">
                    <v-text-field
                        ref="nameField"
                        v-model="camera.name"
                        label="Name"
                        class="camera-name"
                        hide-details="auto"
                    />
                </v-col>
                <v-col cols="12" sm="6" md="2">
                    <v-select
                        :model-value="camera.cameratype"
                        :items="settings.schema.types"
                        label="Type"
                        hide-details
                        @update:model-value="setType"
                    />
                </v-col>
                <v-col cols="12" md="4">
                    <v-select
                        v-if="usesSourceSelect"
                        :model-value="camera.source.trim() || null"
                        :items="sourceItems"
                        :placeholder="sourcePlaceholder"
                        persistent-placeholder
                        label="Source"
                        hide-details
                        @update:model-value="camera.source = $event ?? ''"
                    />
                    <v-text-field
                        v-else
                        v-model="camera.source"
                        :placeholder="sourcePlaceholder"
                        persistent-placeholder
                        label="Source"
                        hide-details
                    />
                </v-col>
                <v-col cols="12" md="3" class="d-flex align-center ga-2 justify-md-end">
                    <v-chip :color="status.color" size="small" variant="tonal">{{ status.text }}</v-chip>
                    <v-btn color="error" variant="tonal" :prepend-icon="mdiDelete" @click="emit('delete', camera)">
                        Delete camera
                    </v-btn>
                </v-col>
            </v-row>
        </v-card-text>

        <v-table density="compact" class="settings-table">
            <thead>
                <tr>
                    <th scope="col">Setting</th>
                    <th scope="col">Requested</th>
                    <th scope="col">Min / Max / Default</th>
                    <th scope="col">Effective</th>
                </tr>
            </thead>
            <tbody>
                <template v-for="row in rows" :key="row.kind + (row.key || row.title)">
                    <tr v-if="row.kind === 'group'" class="group-row">
                        <th colspan="4" scope="colgroup">{{ row.title }}</th>
                    </tr>

                    <tr v-else-if="row.kind === 'edit'">
                        <th scope="row">{{ row.key }}</th>
                        <td class="requested">
                            <v-select
                                v-if="row.key === 'rotate'"
                                :model-value="camera.values.rotate || null"
                                :items="rotateItems()"
                                :placeholder="row.placeholder"
                                :aria-label="`${camera.name} ${row.key} requested`"
                                clearable
                                hide-details
                                @update:model-value="camera.values.rotate = $event ?? ''"
                            />
                            <v-text-field
                                v-else
                                v-model="camera.values[row.key]"
                                :placeholder="row.placeholder"
                                :inputmode="row.numeric ? 'decimal' : undefined"
                                :aria-label="`${camera.name} ${row.key} requested`"
                                hide-details
                            />
                        </td>
                        <td class="text-no-wrap" :class="{ 'text-medium-emphasis': typeof row.bounds === 'string' }">
                            {{ boundsText(row.bounds) }}
                        </td>
                        <td
                            :class="{
                                'text-medium-emphasis': !effective,
                                'text-warning font-weight-bold': isAdjusted(row.key),
                            }"
                            :title="isAdjusted(row.key) ? 'Differs from the requested value' : undefined"
                        >
                            {{ effectiveText(row.key) }}
                        </td>
                    </tr>

                    <tr v-else class="text-medium-emphasis">
                        <th scope="row">{{ row.key }}</th>
                        <td>–</td>
                        <td>–</td>
                        <td>{{ formatValue(row.value) }}</td>
                    </tr>
                </template>
            </tbody>
        </v-table>
    </v-card>
</template>

<style scoped>
.settings-table th[scope="row"] {
    font-weight: normal;
    white-space: nowrap;
}

.settings-table .requested {
    min-width: 200px;
    padding-top: 4px;
    padding-bottom: 4px;
}

.settings-table .group-row th {
    font-weight: bold;
    background: rgba(var(--v-theme-on-surface), 0.06);
}
</style>
