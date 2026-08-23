import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "./App.vue";
import router from "./router";
import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/travel.css";
import "./styles/workflow.css";

createApp(App).use(createPinia()).use(router).mount("#app");
