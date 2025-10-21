// src/main.js
import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './router';
import i18n from './i18n';
import 'element-plus/dist/index.css';
import './assets/styles.css';

// Create app
const app = createApp(App);

// Use plugins
app.use(createPinia());
app.use(router);
app.use(i18n);

// Mount the app
app.mount('#app');