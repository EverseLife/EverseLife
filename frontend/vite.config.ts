import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Слушать все интерфейсы, а не только петлевой: играть заходят с
    // телефона и соседней машины по локальной сети.
    host: true,
    // PORT задаёт превью-харнесс, когда 5173 занят соседним процессом;
    // для игроков переменная не выставлена — адрес остаётся прежним.
    port: Number(process.env.PORT) || 5173,
    // Порт не искать: адрес, который раздали игрокам, не должен меняться
    // от того, что рядом запустился ещё один процесс.
    strictPort: true,
  },
})
