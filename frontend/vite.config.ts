import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Слушать все интерфейсы, а не только петлевой: играть заходят с
    // телефона и соседней машины по локальной сети.
    host: true,
    port: 5173,
    // Порт не искать: адрес, который раздали игрокам, не должен меняться
    // от того, что рядом запустился ещё один процесс.
    strictPort: true,
  },
})
