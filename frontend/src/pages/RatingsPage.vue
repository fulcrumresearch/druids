<script setup>
import { ref, computed, onMounted } from "vue"
import { get } from "../api.js"

const ratings = ref([])
const loading = ref(true)
const error = ref(null)
const expandedHash = ref(null)
const specYaml = ref({})
const loadingYaml = ref(null)

onMounted(async () => {
  await fetchRatings()
})

async function fetchRatings() {
  loading.value = true
  error.value = null
  try {
    const data = await get("/ratings")
    ratings.value = data.ratings
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function toggleSpec(hash) {
  if (expandedHash.value === hash) {
    expandedHash.value = null
    return
  }
  expandedHash.value = hash
  if (!specYaml.value[hash]) {
    loadingYaml.value = hash
    try {
      const data = await get(`/specs/${hash}`)
      specYaml.value[hash] = data.yaml
    } catch (e) {
      specYaml.value[hash] = `Error loading spec: ${e.message}`
    } finally {
      loadingYaml.value = null
    }
  }
}

function formatRating(r) {
  return Math.round(r)
}

function ratingDelta(r) {
  const d = r - 1500
  if (d === 0) return ""
  return d > 0 ? `+${Math.round(d)}` : `${Math.round(d)}`
}

function deltaClass(r) {
  const d = r - 1500
  if (d > 0) return "text-green"
  if (d < 0) return "text-red"
  return "text-dim"
}

function timeAgo(dateStr) {
  if (!dateStr) return ""
  const d = new Date(dateStr)
  const now = new Date()
  const diff = Math.floor((now - d) / 1000)
  if (diff < 60) return "just now"
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

const topRating = computed(() => {
  if (!ratings.value.length) return 1500
  return Math.max(...ratings.value.map(r => r.rating))
})
</script>

<template>
  <div>
    <div class="page-header">
      <h1>Ratings</h1>
      <p>ELO leaderboard for program specs</p>
    </div>

    <div v-if="loading" class="empty-state">
      <span class="spinner"></span>
    </div>

    <div v-else-if="error" class="empty-state text-red">
      {{ error }}
    </div>

    <div v-else-if="!ratings.length" class="empty-state">
      No ratings yet. Ratings are created when PRs from competing executions are merged.
    </div>

    <template v-else>
      <table class="data-table">
        <thead>
          <tr>
            <th style="width: 3rem;">#</th>
            <th>Label</th>
            <th>Hash</th>
            <th style="text-align: right;">Rating</th>
            <th style="text-align: right;">Delta</th>
            <th style="text-align: right;">Matches</th>
            <th>Bar</th>
            <th>Last Updated</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="(r, i) in ratings" :key="r.id">
            <tr
              class="spec-row"
              @click="toggleSpec(r.hash)"
            >
              <td class="text-dim">{{ i + 1 }}</td>
              <td class="text-bright">{{ r.label }}</td>
              <td style="font-family: monospace;" class="text-dim">{{ r.hash }}</td>
              <td style="text-align: right; font-family: monospace;" class="text-bright">
                {{ formatRating(r.rating) }}
              </td>
              <td style="text-align: right; font-family: monospace;" :class="deltaClass(r.rating)">
                {{ ratingDelta(r.rating) }}
              </td>
              <td style="text-align: right;" class="text-dim">
                {{ r.num_comparisons }}
              </td>
              <td style="width: 120px;">
                <div class="rating-bar-track">
                  <div
                    class="rating-bar-fill"
                    :style="{ width: Math.max(5, (r.rating / topRating) * 100) + '%' }"
                  ></div>
                </div>
              </td>
              <td class="text-dim">{{ timeAgo(r.updated_at) }}</td>
            </tr>
            <tr v-if="expandedHash === r.hash" :key="r.id + '-yaml'">
              <td colspan="8" class="yaml-cell">
                <div v-if="loadingYaml === r.hash" class="yaml-loading">
                  <span class="spinner"></span>
                </div>
                <pre v-else class="yaml-content">{{ specYaml[r.hash] }}</pre>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </template>
  </div>
</template>

<style scoped>
.spec-row {
  cursor: pointer;
}

.spec-row:hover {
  background: var(--bg-hover, rgba(255, 255, 255, 0.03));
}

.yaml-cell {
  padding: 0 !important;
}

.yaml-loading {
  padding: 1rem;
  text-align: center;
}

.yaml-content {
  margin: 0;
  padding: 0.75rem 1rem;
  background: var(--bg-secondary, rgba(0, 0, 0, 0.2));
  font-size: 0.78rem;
  line-height: 1.5;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.rating-bar-track {
  height: 6px;
  background: var(--border);
  border-radius: 3px;
  overflow: hidden;
}

.rating-bar-fill {
  height: 100%;
  background: var(--green);
  border-radius: 3px;
  transition: width 0.3s ease;
}

.text-green {
  color: #6abf69;
}
</style>
