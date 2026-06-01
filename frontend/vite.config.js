const path = require("path");
const { defineConfig } = require("vite");
const reactPlugin = require("@vitejs/plugin-react");
const react = reactPlugin.default || reactPlugin;

module.exports = defineConfig({
  plugins: [react()],
  esbuild: {
    loader: "jsx",
  },
  server: {
    port: 3000,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
      "pdfjs-dist": "pdfjs-dist/build/pdf.mjs",
    },
  },
});
