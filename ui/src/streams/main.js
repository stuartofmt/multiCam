import { createApp } from "vue";
import vuetify from "../vuetify.js";
import StreamsApp from "./StreamsApp.vue";

createApp(StreamsApp).use(vuetify).mount("#app");
