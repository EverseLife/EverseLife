// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { applyDensity } from './density'

//: Before the first frame, not after: density decides row heights, and a screen
//: that re-lays itself out once mounted reads as a fault. The planet's theme is
//: applied by App the moment it knows where the body stands.
applyDensity()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
