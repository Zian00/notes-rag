import { QueryClientProvider } from "@tanstack/react-query"
import { BrowserRouter } from "react-router-dom"
import { ThemeProvider } from "next-themes"
import { queryClient } from "@/lib/queryClient"
import { AuthProvider } from "@/auth/AuthContext"
import { Toaster } from "@/components/ui/sonner"
import { AppRoutes } from "@/AppRoutes"

function App() {
  return (
    // attribute="class" toggles the `.dark` class on <html> that index.css's
    // `.dark` selector and `@custom-variant dark` key off of; defaultTheme +
    // enableSystem mean a first-time visitor gets their OS preference until
    // they explicitly pick one (persisted to localStorage by next-themes).
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <BrowserRouter>
            <AppRoutes />
          </BrowserRouter>
          <Toaster />
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>
  )
}

export default App
