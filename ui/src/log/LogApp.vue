<script setup>
// Log page: shows multiCam's logfile and follows it as it grows.
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { mdiDownload, mdiRefresh } from "@mdi/js";
import { getLog } from "../api.js";
import NavTabs from "../NavTabs.vue";

// How often to look for new lines while the page is shown
const POLL_SEC = 2;
// The most text kept on the page: older lines are dropped from the top (Download has the whole log)
const MAX_CHARS = 2_000_000;

const text = ref("");
const path = ref("");
const error = ref("");
const notice = ref("");
// The beginning of the log is not shown (a long log, or older lines dropped)
const truncated = ref(false);
// Keep the newest line in view
const follow = ref(true);
const wrap = ref(false);
const box = ref(null);
let offset;   // byte offset to ask for next (undefined: the end of the log)
let timer = null;
let busy = false;

function atBottom() {
	const el = box.value;
	return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 30;
}

function scrollToEnd() {
	nextTick(() => {
		if (box.value) {
			box.value.scrollTop = box.value.scrollHeight;
		}
	});
}

function addText(more) {
	text.value += more;
	if (text.value.length > MAX_CHARS) {
		const keep = text.value.slice(-Math.floor(MAX_CHARS * 0.75));
		text.value = keep.slice(keep.indexOf("\n") + 1);
		truncated.value = true;
	}
}

async function poll() {
	if (busy || document.hidden) {
		return;
	}
	busy = true;
	try {
		const data = await getLog(offset);
		error.value = "";
		path.value = data.path;
		if (data.restarted) {
			// multiCam restarted: its new log
			text.value = "";
			truncated.value = false;
			notice.value = "multiCam restarted - showing its new log.";
		}
		if (offset === undefined) {
			truncated.value = data.truncated;
		}
		offset = data.offset;
		if (data.text) {
			addText(data.text);
			if (follow.value) {
				scrollToEnd();
			}
		}
	} catch (e) {
		error.value = `Could not read the log: ${e.message}`;
	} finally {
		busy = false;
	}
}

// Start again from the end of the log
async function reload() {
	offset = undefined;
	text.value = "";
	notice.value = "";
	follow.value = true;
	await poll();
	scrollToEnd();
}

// Scrolling up stops following; scrolling back to the end starts it again
function onScroll() {
	follow.value = atBottom();
}

function setFollow(value) {
	follow.value = value;
	if (value) {
		scrollToEnd();
	}
}

onMounted(() => {
	poll();
	timer = setInterval(poll, POLL_SEC * 1000);
	document.addEventListener("visibilitychange", poll);
});

onBeforeUnmount(() => {
	clearInterval(timer);
	document.removeEventListener("visibilitychange", poll);
});
</script>

<template>
	<v-app>
		<v-app-bar flat border="b">
			<NavTabs current="log" />
		</v-app-bar>

		<v-main>
			<v-container fluid class="log-page">
				<p v-if="path" class="text-medium-emphasis log-path mb-2">Logfile: {{ path }}</p>
				<div class="d-flex flex-wrap align-center justify-end ga-2 mb-3">
					<v-switch
						:model-value="follow"
						label="Follow"
						color="primary"
						density="compact"
						hide-details
						class="flex-grow-0 mr-4"
						@update:model-value="setFollow"
					/>
					<v-switch
						v-model="wrap"
						label="Wrap lines"
						color="primary"
						density="compact"
						hide-details
						class="flex-grow-0 mr-2"
					/>
					<v-btn variant="tonal" :prepend-icon="mdiRefresh" @click="reload">Reload</v-btn>
					<v-btn variant="tonal" :prepend-icon="mdiDownload" href="/api/log/download" download>Download</v-btn>
				</div>

				<v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-3">{{ error }}</v-alert>
				<v-alert
					v-if="notice"
					type="info"
					variant="tonal"
					density="compact"
					closable
					class="mb-3"
					@click:close="notice = ''"
				>{{ notice }}</v-alert>

				<v-card>
					<div v-if="truncated" class="text-medium-emphasis text-caption px-3 pt-2">
						The start of the log is not shown here - Download has all of it.
					</div>
					<pre ref="box" class="log-box" :class="{ 'log-wrap': wrap }" @scroll="onScroll">{{ text }}</pre>
				</v-card>
			</v-container>
		</v-main>
	</v-app>
</template>

<style scoped>
.log-path {
	overflow-wrap: anywhere;
}

.log-box {
	margin: 0;
	padding: 12px;
	/* The page's height less the app bar, the path and toolbar lines and margins */
	height: calc(100vh - 220px);
	min-height: 240px;
	overflow: auto;
	font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
	font-size: 0.82rem;
	line-height: 1.4;
	white-space: pre;
}

.log-wrap {
	white-space: pre-wrap;
	overflow-wrap: anywhere;
}
</style>
