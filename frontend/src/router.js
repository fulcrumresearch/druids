import { createRouter, createWebHistory } from 'vue-router'
import { loadUser } from './auth.js'

import HomePage from './pages/HomePage.vue'
import UsagePage from './pages/UsagePage.vue'
import ProgramsPage from './pages/ProgramsPage.vue'
import DocsPage from './pages/DocsPage.vue'
import GraphPage from './pages/GraphPage.vue'
const routes = [
  { path: '/', component: HomePage },
  { path: '/usage', component: UsagePage },
  { path: '/programs/:slug', component: ProgramsPage },
  { path: '/docs/:slug?', component: DocsPage },
  { path: '/graph/:slug', component: GraphPage },
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
