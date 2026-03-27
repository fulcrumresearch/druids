import { ref, watch, onUnmounted } from 'vue'
import type { Ref } from 'vue'

export function useTypewriter(
  sourceRef: Ref<string>,
  { speed = 30 } = {},
): { displayText: Ref<string>; isTyping: Ref<boolean> } {
  const displayText = ref(sourceRef.value || '')
  const isTyping = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null
  let targetText = sourceRef.value || ''
  let cursor = targetText.length

  watch(sourceRef, (newVal) => {
    const text = newVal || ''

    if (text.startsWith(displayText.value)) {
      targetText = text
      startTyping()
    } else {
      displayText.value = ''
      cursor = 0
      targetText = text
      startTyping()
    }
  })

  function startTyping() {
    if (timer) return
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
