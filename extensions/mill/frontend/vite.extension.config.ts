/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: MIT
 */

// The app as an extension of Thinkube Control: one ES module that exports
// mount(), and its stylesheet, in dist/extension/ next to the app. Run with
// NODE_ENV=production: a library build does not set it, and the React plugin
// then compiles JSX for React's development build.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // A library build leaves process.env.NODE_ENV to its consumer; this module runs in the browser as is
  define: {
    'process.env.NODE_ENV': JSON.stringify('production'),
  },
  build: {
    outDir: 'dist/extension',
    emptyOutDir: false,
    sourcemap: false,
    // A library build is not minified unless asked; Control loads this module on every visit
    minify: true,
    lib: {
      entry: fileURLToPath(new URL('./src/extension.tsx', import.meta.url)),
      formats: ['es'],
      fileName: () => 'index.js',
      cssFileName: 'index',
    },
  },
});
