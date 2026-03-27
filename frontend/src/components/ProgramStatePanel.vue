<script setup lang="ts">
defineProps<{
  state: Record<string, unknown>
}>()

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return 'null'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}
</script>

<template>
  <div class="program-state">
    <div class="state-header">state</div>
    <div class="state-entries">
      <div v-for="(value, key) in state" :key="key" class="state-entry">
        <span class="state-key">{{ key }}</span>
        <span class="state-value">{{ formatValue(value) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.program-state {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  line-height: 1.5;
  border: 1px dotted var(--border);
  border-radius: 4px;
  padding: 0.5rem 0.7rem;
  background: var(--bg);
  max-width: 320px;
}

.state-header {
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-dim);
  font-size: 0.6rem;
  margin-bottom: 0.3rem;
}

.state-entries {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.state-entry {
  display: flex;
  gap: 0.5rem;
  align-items: baseline;
}

.state-key {
  color: var(--text-secondary);
  flex-shrink: 0;
}

.state-key::after {
  content: ':';
}

.state-value {
  color: var(--text-bright);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
