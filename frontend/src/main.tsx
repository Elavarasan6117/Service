import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

// Bundled from node_modules, not a CDN — the page has no third-party runtime
// dependency and works on a network that cannot reach the public internet.
import 'leaflet/dist/leaflet.css'
import './styles/app.css'

import App from './App'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
