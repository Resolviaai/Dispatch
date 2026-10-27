import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import { applyTheme, getThemePreference } from './theme';
import { startCloudSessionBootstrap } from './auth/cloudSessionBootstrap';
import './index.css';

applyTheme(getThemePreference());
startCloudSessionBootstrap();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
