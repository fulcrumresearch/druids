<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'

const router = useRouter()
const route = useRoute()
const user = ref(null)

onMounted(() => {
  user.value = router._user || null
})

watch(route, () => {
  user.value = router._user || null
})

const isLoggedIn = computed(() => !!user.value)
const isLoginPage = computed(() => route.path === '/login')

function isActive(path) {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}

function logout() {
  window.location.href = '/api/oauth/logout'
}
</script>

<template>
  <template v-if="isLoginPage">
    <router-view />
  </template>
  <template v-else>
    <nav class="sidebar">
      <div class="sidebar-brand">Orpheus</div>
      <div class="sidebar-nav">
        <router-link to="/" class="sidebar-link" :class="{ active: isActive('/') }">
          Home
        </router-link>
        <router-link to="/setup" class="sidebar-link" :class="{ active: isActive('/setup') }">
          Setup
        </router-link>
        <router-link to="/programs" class="sidebar-link" :class="{ active: isActive('/programs') }">
          Programs
        </router-link>
        <router-link to="/billing" class="sidebar-link" :class="{ active: isActive('/billing') }">
          Billing
        </router-link>
        <router-link to="/ratings" class="sidebar-link" :class="{ active: isActive('/ratings') }">
          Ratings
        </router-link>
        <router-link v-if="user?.is_admin" to="/usage" class="sidebar-link" :class="{ active: isActive('/usage') }">
          Usage
        </router-link>
      </div>
      <div class="sidebar-spacer"></div>
      <div v-if="isLoggedIn" class="sidebar-user">
        {{ user.github_login || 'User' }}
        <br>
        <a href="#" @click.prevent="logout" style="font-size: 0.68rem;">Sign out</a>
      </div>
    </nav>
    <main class="main-content">
      <router-view :key="route.fullPath" />
    </main>
  </template>
</template>
