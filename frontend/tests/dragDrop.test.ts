import { describe, expect, it } from "vitest"
import { resolveDropTarget } from "@/components/layout/sidebar/dragDrop"
import { UNGROUPED_SECTION_ID } from "@/components/layout/sidebar/constants"

describe("resolveDropTarget", () => {
  it("moves an ungrouped chat into a group", () => {
    expect(resolveDropTarget(null, "group-1")).toEqual({ groupId: "group-1" })
  })

  it("moves a grouped chat into a different group", () => {
    expect(resolveDropTarget("group-1", "group-2")).toEqual({ groupId: "group-2" })
  })

  it("ungroups a grouped chat dropped onto the Ungrouped section", () => {
    expect(resolveDropTarget("group-1", UNGROUPED_SECTION_ID)).toEqual({ groupId: null })
  })

  it("is a no-op when dropped onto the group the chat is already in", () => {
    expect(resolveDropTarget("group-1", "group-1")).toBeNull()
  })

  it("is a no-op when an ungrouped chat is dropped onto Ungrouped", () => {
    expect(resolveDropTarget(null, UNGROUPED_SECTION_ID)).toBeNull()
  })
})
