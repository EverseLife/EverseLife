import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Listen on all interfaces, not only loopback: people come to play from a
    // phone and the neighbouring machine over the local network.
    host: true,
    // PORT is set by the preview harness when 5173 is taken by a neighbouring
    // process; for players the variable is not set -- the address stays the same.
    port: Number(process.env.PORT) || 5173,
    // Do not search for a port: the address handed out to players must not
    // change because another process started nearby.

    strictPort: true,
  },
})
