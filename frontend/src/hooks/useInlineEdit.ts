import { useState } from "react"

// Shared "inline text edit" state: an open/closed flag plus the local edit
// value and open()/close() to flip it. Deliberately doesn't own the input's
// ref/autofocus — this project's `react-hooks/refs` lint rule taints an
// entire object once any of its properties looks ref-shaped, so a ref
// returned from a custom hook makes every other property on that same
// object an error to read during render. Each call site still owns its own
// `useRef` + focus effect (see any of the Sidebar rename forms or
// GroupSelect's create form for the two-line pattern); this hook only
// collapses the value/open/close state that doesn't run into that rule.
//
// Submit semantics are deliberately NOT part of this hook either — rename
// vs. create, "noop if unchanged" vs. "noop if empty", and stay-open-vs-close
// on error differ enough across call sites that forcing them into one
// shared submit() would trade real duplication for a leaky abstraction.
export function useInlineEdit() {
  const [isEditing, setIsEditing] = useState(false)
  const [value, setValue] = useState("")

  function open(seedValue = "") {
    setValue(seedValue)
    setIsEditing(true)
  }

  function close(resetValue = "") {
    setValue(resetValue)
    setIsEditing(false)
  }

  return { isEditing, value, setValue, open, close }
}
