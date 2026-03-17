import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { loadUser } from './auth'

import HomePage from './pages/HomePage.vue'
import UsagePage from './pages/UsagePage.vue'
import ExecutionPage from './pages/ExecutionPage.vue'
import SettingsPage from './pages/SettingsPage.vue'
import DocsPage from './pages/DocsPage.vue'

const routes: RouteRecordRaw[] = [
  { path: '/', component: HomePage },
  { path: '/usage', component: UsagePage },
  { path: '/executions/:slug', component: ExecutionPage },
  { path: '/docs/:slug?', component: DocsPage },
  { path: '/settings', component: SettingsPage },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async () => {
  try { await loadUser() } catch {}
  return true
})

export default router
