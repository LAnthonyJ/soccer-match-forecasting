import { defineConfig } from 'vite'

export default defineConfig({
  base: './',
  build: { outDir: '../epl_agent/static', emptyOutDir: true, license: {fileName:'assets/LICENSES.md'} },
  server: { host: '127.0.0.1', proxy: { '/api': 'http://127.0.0.1:5051' } },
})
