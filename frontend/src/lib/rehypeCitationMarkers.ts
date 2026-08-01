import { visitParents } from "unist-util-visit-parents"
import { isInsideCode, type HastNode } from "@/lib/hast"

// Marks the button MessageBubble's `button` component override recognises and
// swaps for the actual clickable citation marker.
export const CITATION_MARKER_CLASS = "chat-citation-marker"

const MARKER_PATTERN = /\[(\d+)\]/g

function markerNode(n: number): HastNode {
  return {
    type: "element",
    tagName: "button",
    properties: { className: [CITATION_MARKER_CLASS], "data-citation-n": String(n) },
    children: [{ type: "text", value: `[${n}]` }],
  }
}

function splitTextWithMarkers(value: string, citationCount: number): HastNode[] {
  const replacement: HastNode[] = []
  let lastEnd = 0
  let match: RegExpExecArray | null
  MARKER_PATTERN.lastIndex = 0
  while ((match = MARKER_PATTERN.exec(value))) {
    const n = Number(match[1])
    if (match.index > lastEnd) {
      replacement.push({ type: "text", value: value.slice(lastEnd, match.index) })
    }
    // Only a marker within the actual citations range becomes clickable — an
    // out-of-range number (an LLM miscount that somehow survives the backend's
    // document-numbering fix, see the shared dedupe_chunks_by_document rule)
    // falls back to plain text rather than linking to the wrong/no source.
    replacement.push(
      n >= 1 && n <= citationCount ? markerNode(n) : { type: "text", value: match[0] }
    )
    lastEnd = match.index + match[0].length
  }
  if (lastEnd < value.length) {
    replacement.push({ type: "text", value: value.slice(lastEnd) })
  }
  return replacement
}

/**
 * Rehype plugin: turn "[n]" substrings in text nodes into clickable citation
 * markers, for n within 1..citationCount. Text inside a `code` element is left
 * untouched — code blocks render via a separate syntax-highlighter path that
 * takes the whole block as one raw string, not hast children.
 *
 * Mutations are collected in a first pass and applied in a second, in reverse
 * document order, rather than splicing while `visitParents` is still walking
 * the tree — mutating a children array mid-traversal shifts indices out from
 * under the walk in progress.
 */
export function rehypeCitationMarkers(citationCount: number) {
  return (tree: HastNode) => {
    if (citationCount <= 0) return

    const targets: Array<{ parent: HastNode; index: number; node: HastNode }> = []

    visitParents(tree as never, "text", (_node, ancestors) => {
      const node = _node as never as HastNode
      const parent = ancestors[ancestors.length - 1] as HastNode | undefined
      if (!parent?.children || !node.value) return
      if (isInsideCode(ancestors)) return
      if (!/\[\d+\]/.test(node.value)) return
      const index = parent.children.indexOf(node)
      if (index === -1) return
      targets.push({ parent, index, node })
    })

    for (let i = targets.length - 1; i >= 0; i--) {
      const { parent, index, node } = targets[i]
      parent.children!.splice(index, 1, ...splitTextWithMarkers(node.value!, citationCount))
    }
  }
}
