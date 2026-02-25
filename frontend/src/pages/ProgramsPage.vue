<script setup>
import { ref, onMounted } from "vue"
import { get, post, del } from "../api.js"

const programs = ref([])
const loading = ref(true)
const error = ref(null)
const expandedHash = ref(null)

const uploading = ref(false)
const uploadError = ref(null)
const yamlInput = ref("")
const labelInput = ref("")

onMounted(async () => {
  await fetchPrograms()
})

async function fetchPrograms() {
  loading.value = true
  error.value = null
  try {
    const data = await get("/user/programs")
    programs.value = data.programs
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function addProgram() {
  if (!yamlInput.value.trim()) return
  uploading.value = true
  uploadError.value = null
  try {
    await post("/user/programs", {
      yaml: yamlInput.value,
      label: labelInput.value || null,
    })
    yamlInput.value = ""
    labelInput.value = ""
    await fetchPrograms()
  } catch (e) {
    uploadError.value = e.message
  } finally {
    uploading.value = false
  }
}

async function removeProgram(hash) {
  try {
    await del(`/programs/${hash}`)
    await fetchPrograms()
  } catch (e) {
    error.value = e.message
  }
}

function toggleYaml(hash) {
  expandedHash.value = expandedHash.value === hash ? null : hash
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
</script>

<template>
  <div>
    <div class="page-header">
      <h1>Programs</h1>
      <p>Manage your registered programs. When you create a task without an explicit program spec, all registered programs run automatically.</p>
    </div>

    <!-- Upload form -->
    <div class="upload-section">
      <h2>Add a program</h2>
      <div class="form-row">
        <input
          v-model="labelInput"
          type="text"
          placeholder="Label (optional)"
          class="form-input label-input"
        />
      </div>
      <div class="form-row">
        <textarea
          v-model="yamlInput"
          placeholder="Paste YAML program spec here..."
          class="form-input yaml-textarea"
          rows="8"
        ></textarea>
      </div>
      <div class="form-row">
        <button
          class="btn btn-primary"
          :disabled="uploading || !yamlInput.trim()"
          @click="addProgram"
        >
          {{ uploading ? "Adding..." : "Add Program" }}
        </button>
      </div>
      <div v-if="uploadError" class="text-red" style="margin-top: 0.5rem;">
        {{ uploadError }}
      </div>
    </div>

    <!-- Program list -->
    <div v-if="loading" class="empty-state">
      <span class="spinner"></span>
    </div>

    <div v-else-if="error" class="empty-state text-red">
      {{ error }}
    </div>

    <div v-else-if="!programs.length" class="empty-state">
      No programs registered. Add a YAML program spec above to get started.
    </div>

    <template v-else>
      <table class="data-table">
        <thead>
          <tr>
            <th>Label</th>
            <th>Hash</th>
            <th style="text-align: right;">Rating</th>
            <th style="text-align: right;">Delta</th>
            <th style="text-align: right;">Matches</th>
            <th>Last Updated</th>
            <th style="width: 5rem;"></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="p in programs" :key="p.id">
            <tr class="program-row" @click="toggleYaml(p.hash)">
              <td class="text-bright">{{ p.label || "(unlabeled)" }}</td>
              <td style="font-family: monospace;" class="text-dim">{{ p.hash }}</td>
              <td style="text-align: right; font-family: monospace;" class="text-bright">
                {{ formatRating(p.rating) }}
              </td>
              <td style="text-align: right; font-family: monospace;" :class="deltaClass(p.rating)">
                {{ ratingDelta(p.rating) }}
              </td>
              <td style="text-align: right;" class="text-dim">
                {{ p.num_comparisons }}
              </td>
              <td class="text-dim">{{ timeAgo(p.updated_at) }}</td>
              <td>
                <button
                  class="btn btn-small btn-danger"
                  @click.stop="removeProgram(p.hash)"
                >
                  Remove
                </button>
              </td>
            </tr>
            <tr v-if="expandedHash === p.hash" :key="p.id + '-yaml'">
              <td colspan="7" class="yaml-cell">
                <pre class="yaml-content">{{ p.yaml }}</pre>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </template>
  </div>
</template>

<style scoped>
.upload-section {
  margin-bottom: 2rem;
  padding: 1rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}

.upload-section h2 {
  margin: 0 0 0.75rem 0;
  font-size: 1rem;
}

.form-row {
  margin-bottom: 0.5rem;
}

.label-input {
  max-width: 300px;
}

.yaml-textarea {
  width: 100%;
  font-family: monospace;
  font-size: 0.8rem;
  resize: vertical;
}

.program-row {
  cursor: pointer;
}

.program-row:hover {
  background: var(--bg-hover, rgba(255, 255, 255, 0.03));
}

.yaml-cell {
  padding: 0 !important;
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

.btn-small {
  padding: 0.2rem 0.5rem;
  font-size: 0.72rem;
}

.btn-danger {
  color: #e57373;
  border-color: #e57373;
  background: transparent;
}

.btn-danger:hover {
  background: rgba(229, 115, 115, 0.1);
}

.text-green {
  color: #6abf69;
}
</style>
