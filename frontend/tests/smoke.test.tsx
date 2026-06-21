import { render, screen } from "@testing-library/react"
import App from "@/App"

// Smoke test: proves the Vitest + jsdom + @testing-library + @/ alias chain all work.
// This is the minimal bar — just confirm the App component mounts without throwing.
describe("App", () => {
  it("mounts without errors", () => {
    render(<App />)
    // The Button in App renders with text content "Notes RAG"
    expect(screen.getByRole("button", { name: /notes rag/i })).toBeInTheDocument()
  })
})
