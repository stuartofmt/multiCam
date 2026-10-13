// Builds the multiCam web interface into the plugin's static folder.
//
//   npm install      once, to install Vue / Vuetify / Vite
//   npm run build    writes index.html, settings.html and assets/ to plugin3.x.x/dsf/static
//   npm run dev      live-reload server at http://localhost:5173/static/ - set MULTICAM_URL
//                    to a running multiCam (default http://localhost:17800) for the API and streams
//
// The built files are committed, so the plugin needs no Node.js and no internet access to run.

import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import vuetify from "vite-plugin-vuetify";

const multiCamUrl = process.env.MULTICAM_URL || "http://localhost:17800";

export default defineConfig({
    // multiCam serves the static folder at /static
    base: "/static/",
    plugins: [
        vue(),
        // Only the Vuetify components that are used end up in the bundle.
        vuetify({ autoImport: true }),
    ],
    build: {
        outDir: fileURLToPath(new URL("../plugin3.x.x/dsf/static", import.meta.url)),
        emptyOutDir: true,
        rollupOptions: {
            input: {
                index: fileURLToPath(new URL("index.html", import.meta.url)),
                settings: fileURLToPath(new URL("settings.html", import.meta.url)),
            },
        },
    },
    server: {
        proxy: {
            // Everything outside /static (API, streams, snapshots) goes to the running multiCam.
            "^/(?!static/).*": { target: multiCamUrl, changeOrigin: true },
        },
    },
});
