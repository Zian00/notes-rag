import { QueryClientProvider } from "@tanstack/react-query"
import { BrowserRouter } from "react-router-dom"
import { queryClient } from "@/lib/queryClient"
import { AuthProvider } from "@/auth/AuthContext"
import { Toaster } from "@/components/ui/sonner"
import { AppRoutes } from "@/AppRoutes"

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
        <Toaster />
      </AuthProvider>
    </QueryClientProvider>
  )
}

export default App
