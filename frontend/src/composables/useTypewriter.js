import { ref, watch, onUnmounted } from 'vue'

/**
 * Typewriter effect for a reactive string.
 * Watches `sourceRef` and types out new characters one at a time.
 *
 * @param {import('vue').Ref<string>} sourceRef - reactive string to type out
 * @param {object} options
 * @param {number} options.speed - ms per character (tunable, default 30)
 * @returns {{ displayText: import('vue').Ref<string>, isTyping: import('vue').Ref<boolean> }}
 */
export function useTypewriter(sourceRef, { speed = 30 } = {}) {
  const displayText = ref(sourceRef.value || '')
  const isTyping = ref(false)
  let timer = null
  let targetText = sourceRef.value || ''
  let cursor = targetText.length

  watch(sourceRef, (newVal) => {
    const text = newVal || ''

    // If the new text starts with what we already displayed, just type the new part
    if (text.startsWith(displayText.value)) {
      targetText = text
      startTyping()
    } else {
      // Completely new text -- reset and type from scratch
      displayText.value = ''
      cursor = 0
      targetText = text
      startTyping()
    }
  })

  function startTyping() {
    if (timer) return // already typing
    if (cursor >= targetText.length) return

    isTyping.value = true
    timer = setInterval(() => {
      if (cursor < targetText.length) {
        cursor++
        displayText.value = targetText.slice(0, cursor)
      } else {
        stopTyping()
      }
    }, speed)
  }

  function stopTyping() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    isTyping.value = false
  }

  onUnmounted(() => {
    stopTyping()
  })

  return { displayText, isTyping }
}
