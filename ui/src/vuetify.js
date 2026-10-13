// Vuetify setup shared by both pages.
import "vuetify/styles";
import "./app.css";
import { createVuetify } from "vuetify";
import { aliases, mdi } from "vuetify/iconsets/mdi-svg";

export default createVuetify({
    icons: {
        defaultSet: "mdi",
        aliases,
        sets: { mdi },
    },
    theme: {
        // Follow the browser / operating system light or dark setting.
        defaultTheme: "system",
        themes: {
            light: {
                dark: false,
                colors: {
                    background: "#f3f4f6",
                    primary: "#1e6fd0",
                },
            },
            dark: {
                dark: true,
                colors: {
                    background: "#111111",
                    surface: "#1e1e1e",
                    primary: "#4a8fe0",
                },
            },
        },
    },
    defaults: {
        VTextField: { variant: "outlined", density: "compact" },
        VSelect: { variant: "outlined", density: "compact" },
        VBtn: { variant: "flat" },
    },
});
