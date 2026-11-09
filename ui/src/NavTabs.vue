<script setup>
// The tabs in every page's app bar: one per page (each page is its own HTML page, so a tab is a link).
import { useDisplay } from "vuetify";
import { mdiCog, mdiTextBoxOutline, mdiVideo } from "@mdi/js";

defineProps({
	// The page being shown: "streams", "settings" or "log"
	current: { type: String, required: true },
});

const TABS = [
	{ value: "streams", href: "/", title: "Camera Streams", icon: mdiVideo },
	{ value: "settings", href: "/settings", title: "Settings", icon: mdiCog },
	{ value: "log", href: "/log", title: "Log", icon: mdiTextBoxOutline },
];

// Icons only on a phone, where the names would not fit
const { xs } = useDisplay();
</script>

<template>
	<!-- flex: none - a toolbar title otherwise shrinks to nothing next to the tabs -->
	<v-app-bar-title v-if="!xs" class="mr-2" style="flex: none">multiCam</v-app-bar-title>
	<v-tabs :model-value="current" color="primary" show-arrows>
		<v-tab
			v-for="tab in TABS"
			:key="tab.value"
			:value="tab.value"
			:href="tab.href"
			:prepend-icon="xs ? undefined : tab.icon"
			:title="tab.title"
		>
			<v-icon v-if="xs" :icon="tab.icon" />
			<template v-else>{{ tab.title }}</template>
		</v-tab>
	</v-tabs>
</template>
