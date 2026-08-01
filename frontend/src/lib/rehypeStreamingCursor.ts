import { visitParents } from "unist-util-visit-parents"
import { isInsideCode, type HastNode } from "@/lib/hast"

// Marks the cursor element so `MessageBubble`'s `span` component override can
// recognise and swap it for the actual animated cursor — kept as a plain HTML
// tag (not a fictional custom tag name) so it survives react-markdown's
// hast-to-JSX conversion without any special-casing there.
export const CURSOR_CLASS = "chat-streaming-cursor"

function cursorNode(): HastNode {
  return {
    type: "element",
    tagName: "span",
    properties: { className: [CURSOR_CLASS] },
    children: [],
  }
}

/**
 * Rehype plugin: splice a cursor marker element right after the tree's true
 * last text node — wherever it is (inside a list item, table cell, etc.) — so
 * the "typing" indicator sits at the actual end of the streamed content,
 * regardless of markdown structure.
 *
 * Text inside a `code` element is treated as opaque: code blocks render via
 * `MessageBubble`'s syntax-highlighter path, which takes the whole block as one
 * raw string rather than reading hast children individually, so splicing a
 * React element into that string would corrupt it. If the overall last text
 * node would land inside a `code` element, the cursor is appended after the
 * whole tree instead (the Q6/Option-A trailing placement), rather than spliced
 * into code's own text.
 */
export function rehypeStreamingCursor() {
  return (tree: HastNode) => {
    let lastTextParent: HastNode | null = null
    let lastTextIndex = -1
    let lastWasInsideCode = false

    visitParents(tree as never, "text", (_node, ancestors) => {
      const node = _node as never as HastNode
      // mdast-to-hast inserts whitespace-only text nodes ("\n") between block
      // elements (e.g. between <li>s) purely for pretty-printing — these are
      // not real content and must not be mistaken for "the last text node",
      // or the cursor lands as a sibling of the block instead of inside it.
      if (!node.value || !node.value.trim()) return
      const parent = ancestors[ancestors.length - 1] as HastNode | undefined
      if (!parent?.children) return
      const index = parent.children.indexOf(node)
      if (index === -1) return
      lastTextParent = parent
      lastTextIndex = index
      lastWasInsideCode = isInsideCode(ancestors)
    })

    if (!lastTextParent) return

    if (lastWasInsideCode) {
      tree.children ??= []
      tree.children.push(cursorNode())
      return
    }

    ;(lastTextParent as HastNode).children!.splice(lastTextIndex + 1, 0, cursorNode())
  }
}
