import { createRouter, createWebHistory } from "vue-router";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: () => import("@/pages/HomePage.vue") },
    { path: "/bind/qq", name: "qq-binding", component: () => import("@/pages/QqBindingPage.vue") },
    { path: "/_setup", name: "setup", component: () => import("@/pages/OwnerSetupPage.vue") },
    { path: "/admin", name: "admin", component: () => import("@/pages/AdminPage.vue") },
    { path: "/travel", name: "travel", component: () => import("@/pages/TravelPlannerPage.vue") },
    { path: "/workflows", name: "workflows", component: () => import("@/pages/WorkflowPage.vue") },
    { path: "/workflows/:workflowId", name: "workflow-detail", component: () => import("@/pages/WorkflowPage.vue") },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});

export default router;
