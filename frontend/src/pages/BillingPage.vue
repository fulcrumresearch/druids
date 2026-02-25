<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { get, post } from '../api.js'

const route = useRoute()
const router = useRouter()
const user = ref(null)
const loading = ref(true)
const error = ref(null)
const redirecting = ref(false)
const showSuccess = computed(() => route.query.success === '1')

const statusLabel = computed(() => {
  const s = user.value?.subscription_status
  if (s === 'active') return 'Active'
  if (s === 'past_due') return 'Past due'
  if (s === 'canceled') return 'Canceled'
  return 'Free tier'
})

const statusClass = computed(() => {
  const s = user.value?.subscription_status
  if (s === 'active') return 'text-green'
  if (s === 'past_due') return 'text-yellow'
  return 'text-dim'
})

const isActive = computed(() => user.value?.subscription_status === 'active')

const executionCount = computed(() => user.value?.execution_count ?? 0)
const freeLimit = computed(() => user.value?.free_tier_reviews ?? 15)
const freeRemaining = computed(() => Math.max(0, freeLimit.value - executionCount.value))
const isLimitReached = computed(() => !isActive.value && executionCount.value >= freeLimit.value)

async function fetchUser() {
  const data = await get('/me')
  user.value = data
  router._user = data
  return data
}

onMounted(async () => {
  try {
    const data = await fetchUser()
    if (showSuccess.value && data.subscription_status !== 'active') {
      // Webhook may not have arrived yet. Poll a few times.
      let attempts = 0
      const poll = setInterval(async () => {
        attempts++
        const updated = await fetchUser()
        if (updated.subscription_status === 'active' || attempts >= 10) {
          clearInterval(poll)
        }
      }, 2000)
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

async function subscribe() {
  error.value = null
  redirecting.value = true
  try {
    const data = await post('/billing/checkout')
    window.location.href = data.url
  } catch (e) {
    error.value = e.message
    redirecting.value = false
  }
}

function manage() {
  window.location.href = '/api/billing/portal'
}
</script>

<template>
  <div>
    <div class="page-header">
      <h1>Billing</h1>
      <p>Manage your subscription</p>
    </div>

    <div v-if="showSuccess" class="card mb-2" style="border-color: var(--green);">
      <span class="text-green">Subscription activated. You're all set.</span>
    </div>

    <div v-if="error" class="card mb-2" style="border-color: var(--red);">
      <span class="text-red">{{ error }}</span>
    </div>

    <div v-if="loading" class="empty-state">
      <span class="spinner"></span>
    </div>

    <template v-else-if="user">
      <!-- Usage card -->
      <div class="card mb-2">
        <div style="display: flex; justify-content: space-between; align-items: baseline;">
          <h3>Usage</h3>
          <span class="text-dim" style="font-size: 0.75rem;">
            {{ executionCount }} review{{ executionCount === 1 ? '' : 's' }} used
          </span>
        </div>
        <div v-if="!isActive" class="mt-1">
          <div style="background: var(--bg-secondary); border-radius: 4px; height: 6px; overflow: hidden;">
            <div
              :style="{
                width: Math.min(100, (executionCount / freeLimit) * 100) + '%',
                height: '100%',
                background: isLimitReached ? 'var(--red)' : 'var(--green)',
                transition: 'width 0.3s',
              }"
            ></div>
          </div>
          <div class="text-dim mt-1" style="font-size: 0.72rem;">
            <template v-if="isLimitReached">
              Free tier limit reached. Subscribe to continue.
            </template>
            <template v-else>
              {{ freeRemaining }} of {{ freeLimit }} free reviews remaining
            </template>
          </div>
        </div>
        <div v-else class="text-dim mt-1" style="font-size: 0.72rem;">
          Unlimited reviews with active subscription
        </div>
      </div>

      <!-- Subscription card -->
      <div class="card mb-2">
        <div style="display: flex; justify-content: space-between; align-items: baseline;">
          <h3>Subscription</h3>
          <span :class="statusClass" style="font-size: 0.75rem;">{{ statusLabel }}</span>
        </div>
        <div class="text-secondary mt-1" style="font-size: 0.75rem;">
          $25/mo for unlimited PR reviews
        </div>
        <div class="mt-2">
          <button
            v-if="!isActive"
            class="btn btn-primary"
            :disabled="redirecting"
            @click="subscribe"
          >
            {{ redirecting ? 'Redirecting...' : 'Subscribe' }}
          </button>
          <button
            v-else
            class="btn btn-secondary"
            @click="manage"
          >
            Manage subscription
          </button>
        </div>
      </div>
    </template>
  </div>
</template>
