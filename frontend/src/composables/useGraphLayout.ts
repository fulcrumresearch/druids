import { computed } from 'vue'
import type { Ref, ComputedRef } from 'vue'
import type { Edge, Position, LayoutOptions } from '../types'

interface AgentEntry {
  name: string
}

/**
 * DAG layout composable. Takes reactive agent list and edge list,
 * returns reactive positions map and container dimensions.
 *
 * Layout: left-to-right DAG. Toposort assigns depth (column), then
 * barycenter ordering within columns minimizes edge crossings.
 */
export function useGraphLayout(
  agents: Ref<AgentEntry[]>,
  edges: Ref<Edge[]>,
  opts: LayoutOptions = {},
): {
  positions: ComputedRef<Map<string, Position>>
  containerWidth: ComputedRef<number>
  containerHeight: ComputedRef<number>
} {
  const {
    columnSpacing = 400,
    rowSpacing = 160,
    paddingX = 200,
    paddingY = 250,
  } = opts

  const positions = computed(() => {
    const names = agents.value.map(a => a.name)
    const edgeList = edges.value

    if (!names.length) return new Map<string, Position>()

    // Build adjacency, breaking cycles via DFS so the toposort terminates.
    const children = new Map<string, string[]>()
    const parents = new Map<string, string[]>()
    for (const name of names) {
      children.set(name, [])
      parents.set(name, [])
    }

    // DFS cycle detection — start from highest-out-degree node so the
    // "orchestrator" becomes the tree root and its outgoing edges stay
    // as tree edges instead of being misclassified as back-edges.
    const WHITE = 0, GRAY = 1, BLACK = 2
    const color = new Map<string, number>()
    for (const name of names) color.set(name, WHITE)
    const backEdges = new Set<string>()

    const allEdges = new Map<string, string[]>()
    for (const name of names) allEdges.set(name, [])
    for (const { from, to } of edgeList) {
      if (allEdges.has(from)) allEdges.get(from)!.push(to)
    }

    function dfs(node: string) {
      color.set(node, GRAY)
      for (const next of (allEdges.get(node) || [])) {
        if (!color.has(next)) continue
        if (color.get(next) === GRAY) {
          backEdges.add(`${node}->${next}`)
        } else if (color.get(next) === WHITE) {
          dfs(next)
        }
      }
      color.set(node, BLACK)
    }
    const sorted = [...names].sort((a, b) =>
      (allEdges.get(b)?.length || 0) - (allEdges.get(a)?.length || 0)
    )
    for (const name of sorted) {
      if (color.get(name) === WHITE) dfs(name)
    }

    // Build DAG adjacency excluding back-edges
    for (const { from, to } of edgeList) {
      if (backEdges.has(`${from}->${to}`)) continue
      if (children.has(from) && parents.has(to)) {
        children.get(from)!.push(to)
        parents.get(to)!.push(from)
      }
    }

    // BFS ranking: assign depth = shortest path from any root.
    // This keeps cyclic graphs wide and shallow instead of stretching
    // them into deep single-file chains (which longest-path does).
    const depth = new Map<string, number>()
    const queue: string[] = []
    for (const name of names) {
      if (parents.get(name)!.length === 0) {
        depth.set(name, 0)
        queue.push(name)
      }
    }
    let head = 0
    while (head < queue.length) {
      const node = queue[head++]
      for (const child of children.get(node)!) {
        if (!depth.has(child)) {
          depth.set(child, depth.get(node)! + 1)
          queue.push(child)
        }
      }
    }

    // Handle any nodes not reached (disconnected)
    for (const name of names) {
      if (!depth.has(name)) depth.set(name, 0)
    }

    // Group into columns by depth
    const columns = new Map<number, string[]>()
    for (const name of names) {
      const d = depth.get(name)!
      if (!columns.has(d)) columns.set(d, [])
      columns.get(d)!.push(name)
    }

    const maxDepth = Math.max(...columns.keys())

    // Barycenter ordering: 4 passes to minimize crossings
    for (const [d, col] of columns) {
      columns.set(d, [...col])
    }

    for (let pass = 0; pass < 4; pass++) {
      const leftToRight = pass % 2 === 0
      const depthRange = leftToRight
        ? range(0, maxDepth + 1)
        : range(maxDepth, -1)

      for (const d of depthRange) {
        const col = columns.get(d)
        if (!col || col.length <= 1) continue

        const adjacentDepth = leftToRight ? d - 1 : d + 1
        const adjacentCol = columns.get(adjacentDepth)
        if (!adjacentCol) continue

        const adjacentIndex = new Map<string, number>()
        adjacentCol.forEach((name, i) => adjacentIndex.set(name, i))

        const bary = new Map<string, number>()
        for (const name of col) {
          const neighbors = leftToRight ? parents.get(name)! : children.get(name)!
          const inAdjacentCol = neighbors.filter((n: string) => adjacentIndex.has(n))
          if (inAdjacentCol.length > 0) {
            const avg = inAdjacentCol.reduce((sum: number, n: string) => sum + adjacentIndex.get(n)!, 0) / inAdjacentCol.length
            bary.set(name, avg)
          } else {
            bary.set(name, col.indexOf(name))
          }
        }

        col.sort((a, b) => bary.get(a)! - bary.get(b)!)
      }
    }

    // Convert to pixel coordinates
    const result = new Map<string, Position>()

    // Single column (no edges): lay out horizontally instead of stacking vertically
    if (maxDepth === 0) {
      const col = columns.get(0)!
      const totalWidth = (col.length - 1) * columnSpacing
      const startX = paddingX + totalWidth / 2 - totalWidth / 2
      col.forEach((name, i) => {
        result.set(name, { x: startX + i * columnSpacing, y: paddingY })
      })
      return result
    }

    for (const [d, col] of columns) {
      const x = paddingX + d * columnSpacing
      const totalHeight = (col.length - 1) * rowSpacing
      const startY = paddingY + (maxColumnSize(columns) - 1) * rowSpacing / 2 - totalHeight / 2
      col.forEach((name, i) => {
        result.set(name, { x, y: startY + i * rowSpacing })
      })
    }

    return result
  })

  const containerWidth = computed(() => {
    if (!positions.value.size) return 0
    let maxX = 0
    for (const { x } of positions.value.values()) {
      if (x > maxX) maxX = x
    }
    return maxX + paddingX
  })

  const containerHeight = computed(() => {
    if (!positions.value.size) return 0
    let maxY = 0
    for (const { y } of positions.value.values()) {
      if (y > maxY) maxY = y
    }
    return maxY + paddingY
  })

  return { positions, containerWidth, containerHeight }
}

function range(start: number, end: number): number[] {
  const result: number[] = []
  if (start < end) {
    for (let i = start; i < end; i++) result.push(i)
  } else {
    for (let i = start; i > end; i--) result.push(i)
  }
  return result
}

function maxColumnSize(columns: Map<number, string[]>): number {
  let max = 0
  for (const col of columns.values()) {
    if (col.length > max) max = col.length
  }
  return max
}
