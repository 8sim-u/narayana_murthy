import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource/host-grotesk/400.css'
import '@fontsource/host-grotesk/500.css'
import '@fontsource/host-grotesk/600.css'
import '@fontsource/host-grotesk/700.css'
import '@fontsource/fustat/400.css'
import '@fontsource/fustat/500.css'
import '@fontsource/fustat/600.css'
import '@fontsource/commit-mono/400.css'
import '@fontsource/commit-mono/500.css'
import App from './App.tsx'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
