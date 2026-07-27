import { createRouter, createWebHistory } from "vue-router";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: () => import("@/pages/HomePage.vue") },
    { path: "/_setup", name: "setup", component: () => import("@/pages/OwnerSetupPage.vue") },
    { path: "/admin", name: "admin", component: () => import("@/pages/AdminPage.vue") },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});

export default router;
