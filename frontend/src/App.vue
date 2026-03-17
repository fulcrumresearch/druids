<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuth } from './auth'

const route = useRoute()
const { user } = useAuth()

function isActive(path: string): boolean {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}
</script>

<template>
  <nav class="sidebar">
    <div class="sidebar-brand">Druids</div>
    <div class="sidebar-nav">
      <router-link to="/" class="sidebar-link" :class="{ active: isActive('/') }">
        Home
      </router-link>
      <router-link to="/docs" class="sidebar-link" :class="{ active: isActive('/docs') }">
        Docs
      </router-link>
    </div>
    <div class="sidebar-spacer"></div>
    <div v-if="user" class="sidebar-user">
      {{ user.github_login || 'local' }}
    </div>
  </nav>
  <main class="main-content">
    <router-view v-slot="{ Component }">
      <keep-alive>
        <component :is="Component" :key="route.path" />
      </keep-alive>
    </router-view>
  </main>
</template>
