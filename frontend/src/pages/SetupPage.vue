<script setup>
import { ref, computed, onUnmounted } from 'vue'
import { get, post } from '../api.js'
import { statusClass } from '../utils.js'

const currentStep = ref(1)
const totalSteps = 4

const repoName = ref('')
const sessionId = ref(null)
const session = ref(null)
const loading = ref(false)
const error = ref(null)
const pollingInterval = ref(null)
const showConfirmDialog = ref(false)
const retrying = ref(false)

const stepTitles = [
  'Connect repository',
  'Configure VM',
  'Verify setup',
  'Save snapshot'
]

const isStepComplete = (step) => currentStep.value > step
const isStepCurrent = (step) => currentStep.value === step
const isStepPending = (step) => currentStep.value < step

async function startSession() {
  if (!repoName.value.trim()) return

  loading.value = true
  error.value = null

  try {
    const data = await post('/setup/sessions', {
      repo_full_name: repoName.value.trim()
    })
    sessionId.value = data.session_id
    session.value = data
    currentStep.value = 2
    startPolling()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function pollSession() {
  if (!sessionId.value) return

  try {
    const data = await get(`/setup/sessions/${sessionId.value}`)
    session.value = data

    if (data.status === 'failed' || data.status === 'ready' || data.status === 'completed') {
      stopPolling()
    }
  } catch (e) {
    console.error('Poll error:', e)
    stopPolling()
  }
}

function startPolling() {
  if (pollingInterval.value) return
  pollingInterval.value = setInterval(pollSession, 3000)
}

function stopPolling() {
  if (pollingInterval.value) {
    clearInterval(pollingInterval.value)
    pollingInterval.value = null
  }
}

async function retrySetup() {
  if (!sessionId.value) return

  retrying.value = true
  error.value = null

  try {
    const data = await post(`/setup/sessions/${sessionId.value}/retry`)
    session.value = data
    startPolling()
  } catch (e) {
    error.value = e.message
  } finally {
    retrying.value = false
  }
}

function proceedToVerify() {
  currentStep.value = 3
}

function proceedToSave() {
  showConfirmDialog.value = true
}

function cancelSave() {
  showConfirmDialog.value = false
}

async function confirmSave() {
  showConfirmDialog.value = false
  loading.value = true

  try {
    await post(`/setup/sessions/${sessionId.value}/snapshot`)
    currentStep.value = 4
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <div>
    <div class="page-header">
      <h1>Setup wizard</h1>
      <p>Configure a new devbox for your repository</p>
    </div>

    <!-- Progress indicator -->
    <div class="progress-indicator mb-3">
      <div
        v-for="step in totalSteps"
        :key="step"
        class="progress-step"
        :class="{
          'progress-step-complete': isStepComplete(step),
          'progress-step-current': isStepCurrent(step),
          'progress-step-pending': isStepPending(step)
        }"
      >
        <div class="progress-step-number">{{ step }}</div>
        <div class="progress-step-title">{{ stepTitles[step - 1] }}</div>
      </div>
    </div>

    <!-- Step 1: Connect repo -->
    <div v-if="currentStep === 1" class="card">
      <h2 class="mb-2">Step 1: Connect repository</h2>
      <p class="text-secondary mb-2">Enter the full name of the repository you want to set up.</p>

      <form @submit.prevent="startSession">
        <div class="mb-2">
          <label>Repository name</label>
          <input
            v-model="repoName"
            placeholder="owner/repo"
            :disabled="loading"
          />
        </div>

        <div v-if="error" class="error-message mb-2">
          {{ error }}
        </div>

        <button
          class="btn btn-primary"
          type="submit"
          :disabled="loading || !repoName.trim()"
        >
          {{ loading ? 'Starting...' : 'Start setup' }}
        </button>
      </form>
    </div>

    <!-- Step 2: Configure -->
    <div v-if="currentStep === 2" class="card">
      <h2 class="mb-2">Step 2: Configure VM</h2>

      <div v-if="!session || session.status === 'provisioning' || session.status === 'starting'">
        <div class="flex items-center gap-1 mb-2">
          <span class="spinner"></span>
          <span>Provisioning VM...</span>
        </div>
        <p class="text-secondary" style="font-size: 0.8rem;">
          This may take a minute or two. Please wait.
        </p>
      </div>

      <div v-else-if="session.status === 'failed'" class="error-state">
        <h3 class="text-red mb-1">Setup failed</h3>
        <p class="text-secondary mb-2">{{ session.error_message || 'An unknown error occurred' }}</p>
        <button
          class="btn btn-secondary"
          @click="retrySetup"
          :disabled="retrying"
        >
          {{ retrying ? 'Retrying...' : 'Retry' }}
        </button>
      </div>

      <div v-else>
        <div class="success-message mb-2">
          VM has been provisioned successfully.
        </div>

        <p class="text-secondary mb-2">Use SSH to connect and install dependencies.</p>

        <div v-if="session.ssh_info" class="ssh-info mb-2">
          <label>SSH connection</label>
          <div class="ssh-command-wrapper">
            <code class="ssh-command">{{ session.ssh_info }}</code>
          </div>
        </div>

        <button
          class="btn btn-primary"
          @click="proceedToVerify"
        >
          Continue to verification
        </button>
      </div>
    </div>

    <!-- Step 3: Verify -->
    <div v-if="currentStep === 3" class="card">
      <h2 class="mb-2">Step 3: Verify setup</h2>

      <div v-if="session">
        <div class="verification-info mb-2">
          <div class="info-row">
            <span class="info-label">Repository:</span>
            <span>{{ session.repo_full_name }}</span>
          </div>
          <div class="info-row">
            <span class="info-label">Status:</span>
            <span class="badge" :class="statusClass(session.status)">
              {{ session.status }}
            </span>
          </div>
          <div v-if="session.ssh_info" class="info-row">
            <span class="info-label">SSH:</span>
            <code style="font-size: 0.75rem;">{{ session.ssh_info }}</code>
          </div>
        </div>

        <p class="text-secondary mb-2">
          Ensure all dependencies are installed and the environment is ready before proceeding.
        </p>

        <button
          class="btn btn-primary"
          @click="proceedToSave"
        >
          Ready to save
        </button>
      </div>
    </div>

    <!-- Step 4: Save -->
    <div v-if="currentStep === 4" class="card">
      <h2 class="mb-2">Step 4: Save snapshot</h2>

      <div class="text-green mb-2">
        Snapshot saved successfully!
      </div>

      <p class="text-secondary">
        Your devbox is now ready to use. You can start executions using this snapshot.
      </p>
    </div>

    <!-- Confirmation dialog -->
    <div v-if="showConfirmDialog" class="modal-overlay" @click="cancelSave">
      <div class="modal-card" @click.stop>
        <h3 class="mb-2">Confirm snapshot</h3>
        <p class="text-secondary mb-2">
          You are about to snapshot the VM. This will save the current state of the environment.
          Make sure all dependencies are installed and configured correctly.
        </p>
        <div class="flex gap-1">
          <button class="btn btn-primary" @click="confirmSave" :disabled="loading">
            {{ loading ? 'Saving...' : 'Confirm' }}
          </button>
          <button class="btn btn-secondary" @click="cancelSave" :disabled="loading">
            Cancel
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.progress-indicator {
  display: flex;
  gap: 1rem;
  padding: 1rem;
  background: var(--bg-card);
  border: 1px dotted var(--border);
  border-radius: 2px;
}

.progress-step {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
}

.progress-step-number {
  width: 2rem;
  height: 2rem;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.85rem;
  font-weight: 600;
  border: 1px dotted var(--border);
  background: var(--bg-card);
}

.progress-step-title {
  font-size: 0.72rem;
  text-align: center;
  color: var(--text-secondary);
}

.progress-step-complete .progress-step-number {
  background: var(--green);
  color: var(--bg);
  border-color: var(--green);
}

.progress-step-complete .progress-step-title {
  color: var(--green);
}

.progress-step-current .progress-step-number {
  background: var(--text);
  color: var(--bg);
  border-color: var(--text);
}

.progress-step-current .progress-step-title {
  color: var(--text-bright);
  font-weight: 600;
}

.progress-step-pending .progress-step-number {
  color: var(--text-dim);
}

.error-message {
  padding: 0.75rem;
  background: rgba(163, 64, 58, 0.1);
  border: 1px dotted var(--red);
  border-radius: 2px;
  color: var(--red);
  font-size: 0.85rem;
}

.error-state {
  padding: 1rem;
  background: rgba(163, 64, 58, 0.05);
  border: 1px dotted var(--red);
  border-radius: 2px;
}

.success-message {
  padding: 0.75rem;
  background: rgba(45, 122, 62, 0.1);
  border: 1px dotted var(--green);
  border-radius: 2px;
  color: var(--green);
  font-size: 0.85rem;
}

.ssh-info {
  padding: 1rem;
  background: var(--bg-terminal);
  border: 1px dotted var(--border);
  border-radius: 2px;
}

.ssh-command-wrapper {
  background: rgba(255, 255, 255, 0.3);
  padding: 0.5rem;
  border-radius: 2px;
  margin-top: 0.5rem;
}

.ssh-command {
  display: block;
  font-size: 0.8rem;
  color: var(--text-bright);
  word-break: break-all;
}

.verification-info {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.info-row {
  display: flex;
  gap: 0.75rem;
  font-size: 0.85rem;
}

.info-label {
  font-weight: 600;
  color: var(--text-secondary);
  min-width: 80px;
}

.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-card {
  background: var(--bg);
  border: 1px dotted var(--border);
  border-radius: 2px;
  padding: 2rem;
  max-width: 500px;
  width: 90%;
}

@media (max-width: 720px) {
  .progress-indicator {
    flex-direction: column;
    gap: 0.75rem;
  }

  .progress-step {
    flex-direction: row;
    justify-content: flex-start;
  }

  .progress-step-title {
    text-align: left;
  }
}
</style>
