import { createRouter, createWebHashHistory } from 'vue-router'
import { get } from './api.js'

import LoginPage from './pages/LoginPage.vue'
import HomePage from './pages/HomePage.vue'
import SetupPage from './pages/SetupPage.vue'
import BillingPage from './pages/BillingPage.vue'
import UsagePage from './pages/UsagePage.vue'
import RatingsPage from './pages/RatingsPage.vue'
import ProgramsPage from './pages/ProgramsPage.vue'
const routes = [
  { path: '/login', component: LoginPage, meta: { public: true } },
  { path: '/', component: HomePage },
  { path: '/setup', component: SetupPage },
  { path: '/setup/:owner/:repo', component: SetupPage },
  { path: '/billing', component: BillingPage },
  { path: '/usage', component: UsagePage },
  { path: '/programs', component: ProgramsPage },
  { path: '/ratings', component: RatingsPage },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

let cachedUser = null

router.beforeEach(async (to) => {
  if (to.meta.public) return true

  if (cachedUser) {
    router._user = cachedUser
    return true
  }

  try {
    const user = await get('/me')
    cachedUser = user
    router._user = user
    return true
  } catch {
    return { path: '/login' }
  }
})

export default router
