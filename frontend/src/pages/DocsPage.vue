<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import type { DocSection } from '../types'

const route = useRoute()
const router = useRouter()

const docs: Record<string, DocSection> = {
  howto: {
    label: 'How-to',
    pages: {
      'get-started': { title: 'Get started', file: 'howto/get-started.md' },
      'write-a-program': { title: 'Write a program', file: 'howto/write-a-program.md' },
      'run-locally': { title: 'Run locally', file: 'howto/run-locally.md' },
    },
  },
  concepts: {
    label: 'Concepts',
    pages: {
      'program-design': { title: 'Program design', file: 'explanation/program-design.md' },
    },
  },
  tutorials: {
    label: 'Tutorials',
    pages: {
      'build-flow': { title: 'Build flow', file: 'tutorials/build-flow.md' },
      'client-events': { title: 'Client events', file: 'tutorials/client-events.md' },
    },
  },
  reference: {
    label: 'Reference',
    pages: {
      'program-api': { title: 'Program API', file: 'reference/program-api.md' },
      'ctx': { title: 'ctx', file: 'reference/ctx.md' },
      'agent': { title: 'agent', file: 'reference/agent.md' },
      'client-api': { title: 'Client API', file: 'reference/client-api.md' },
      'git-permissions': { title: 'Git permissions', file: 'reference/git-permissions.md' },
      'built-in-tools': { title: 'Built-in tools', file: 'reference/built-in-tools.md' },
      'cli': { title: 'CLI', file: 'reference/cli.md' },
      'configuration': { title: 'Configuration', file: 'reference/configuration.md' },
    },
  },
}

// Import all markdown files at build time
const mdFiles = import.meta.glob('/docs/**/*.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

const currentSlug = computed(() => {
  const slug = route.params.slug
  return (Array.isArray(slug) ? slug[0] : slug) || 'get-started'
})

const currentPage = computed(() => {
  for (const [, section] of Object.entries(docs)) {
    if (section.pages[currentSlug.value]) return section.pages[currentSlug.value]
  }
  return docs.tutorials.pages['build-flow']
})

const renderedHtml = computed(() => {
  const page = currentPage.value
  if (!page) return ''
  const key = `/docs/${page.file}`
  const raw = mdFiles[key]
  if (!raw) return '<p class="text-secondary">No content.</p>'
  return marked(raw) as string
})

const wordCount = computed(() => {
  const page = currentPage.value
  if (!page) return 0
  const key = `/docs/${page.file}`
  const raw = mdFiles[key]
  if (!raw) return 0
  return raw.split(/\s+/).filter((w: string) => w.length > 0).length
})

function navigate(slug: string) {
  router.push(`/docs/${slug}`)
}

</script>

<template>
  <div class="docs-layout">
    <nav class="docs-nav">
      <div v-for="(section, sectionKey) in docs" :key="sectionKey" class="docs-nav-section">
        <div class="docs-nav-label">{{ section.label }}</div>
        <a
          v-for="(page, slug) in section.pages"
          :key="slug"
          class="docs-nav-link"
          :class="{ active: currentSlug === slug }"
          @click.prevent="navigate(slug)"
          href="#"
        >
          {{ page.title }}
        </a>
      </div>
    </nav>
    <div class="docs-main">
      <div class="version-bar">
        <span class="version-meta">{{ wordCount }} words</span>
      </div>
      <div class="docs-body">
        <div class="docs-content" v-html="renderedHtml"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.docs-layout {
  display: flex;
  gap: 0;
  width: 100%;
  min-height: calc(100vh - 3rem);
}

.docs-nav {
  width: 200px;
  flex-shrink: 0;
  border-right: 1px dotted var(--border);
  padding: 1.5rem 0 1.5rem 0;
  overflow-y: auto;
}

.docs-nav-section {
  margin-bottom: 1.25rem;
}

.docs-nav-label {
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-dim);
  padding: 0.25rem 1rem;
  margin-bottom: 0.15rem;
}

.docs-nav-link {
  display: block;
  padding: 0.3rem 1rem;
  font-size: 0.78rem;
  color: var(--text-secondary);
  text-decoration: none;
  cursor: pointer;
  transition: color 0.1s ease;
}

.docs-nav-link:hover {
  color: var(--text-bright);
  opacity: 1;
}

.docs-nav-link.active {
  color: var(--text-bright);
  border-left: 2px solid var(--text);
  padding-left: calc(1rem - 2px);
}

.docs-main {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.version-bar {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.6rem 2.5rem;
  border-bottom: 1px dotted var(--border-light);
  flex-wrap: wrap;
  flex-shrink: 0;
}

.version-meta {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 0.62rem;
  color: var(--text-dim);
}

.docs-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.docs-content {
  flex: 1;
  padding: 1.5rem 2.5rem;
  max-width: 52rem;
  overflow-y: auto;
}

@media (max-width: 720px) {
  .docs-layout {
    flex-direction: column;
  }
  .docs-nav {
    width: 100%;
    border-right: none;
    border-bottom: 1px dotted var(--border);
    padding: 0.75rem;
    display: flex;
    gap: 1rem;
    overflow-x: auto;
  }
  .docs-nav-section {
    margin-bottom: 0;
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }
  .docs-nav-label {
    display: none;
  }
  .docs-nav-link {
    white-space: nowrap;
    padding: 0.3rem 0.5rem;
    font-size: 0.72rem;
  }
  .docs-nav-link.active {
    border-left: none;
    border-bottom: 2px solid var(--text);
    padding-left: 0.5rem;
  }
  .version-bar {
    padding: 0.5rem 1rem;
  }
  .docs-content {
    padding: 1.25rem 1rem;
  }
}
</style>

<style>
/* Markdown rendering styles (unscoped so they apply to v-html) */
.docs-content h1 {
  font-family: var(--font-serif);
  font-style: italic;
  font-weight: 400;
  font-size: clamp(1.4rem, 3.5vw, 1.8rem);
  color: var(--text-bright);
  margin-bottom: 0.75rem;
  line-height: 1.25;
}

.docs-content h2 {
  font-family: var(--font-serif);
  font-style: italic;
  font-weight: 400;
  font-size: clamp(1.05rem, 2.5vw, 1.3rem);
  color: var(--text-bright);
  margin-top: 2.5rem;
  margin-bottom: 0.6rem;
  padding-top: 1.5rem;
  border-top: 1px dotted var(--border-light);
  line-height: 1.25;
}

.docs-content h3 {
  font-family: var(--font-serif);
  font-style: italic;
  font-weight: 400;
  font-size: clamp(0.92rem, 2vw, 1.05rem);
  color: var(--text-bright);
  margin-top: 1.75rem;
  margin-bottom: 0.5rem;
  line-height: 1.3;
}

.docs-content p {
  color: var(--text);
  line-height: 1.75;
  margin-bottom: 1rem;
  font-size: 0.85rem;
}

.docs-content ul, .docs-content ol {
  margin-bottom: 1rem;
  padding-left: 1.5rem;
}

.docs-content li {
  color: var(--text);
  font-size: 0.85rem;
  line-height: 1.7;
  margin-bottom: 0.25rem;
}

.docs-content a {
  color: var(--text);
  text-decoration: underline;
  text-underline-offset: 2px;
  text-decoration-color: var(--border);
}

.docs-content a:hover {
  text-decoration-color: var(--text);
  opacity: 1;
}

.docs-content code {
  font-family: var(--font-mono);
  font-size: 0.82em;
  background: var(--bg-terminal);
  padding: 0.12rem 0.35rem;
  border-radius: 2px;
  color: var(--text);
}

.docs-content pre {
  background: var(--bg-terminal);
  border: 1px dotted var(--border);
  border-radius: 2px;
  padding: 1rem;
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: clamp(0.72rem, 1.4vw, 0.8rem);
  line-height: 1.55;
  margin-bottom: 1rem;
  color: var(--text);
}

.docs-content pre code {
  background: none;
  padding: 0;
  border-radius: 0;
  font-size: inherit;
}

.docs-content table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 1rem;
  font-size: 0.82rem;
}

.docs-content th {
  text-align: left;
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-secondary);
  padding: 0.5rem 0.75rem;
  border-bottom: 1px dotted var(--border);
}

.docs-content td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px dotted var(--border-light);
  color: var(--text);
}

.docs-content blockquote {
  border-left: 2px dotted var(--border);
  padding-left: 1rem;
  color: var(--text-secondary);
  margin-bottom: 1rem;
}

.docs-content hr {
  border: none;
  border-top: 1px dotted var(--border);
  margin: 2rem 0;
}

.docs-content strong {
  color: var(--text-bright);
  font-weight: 600;
}
</style>
