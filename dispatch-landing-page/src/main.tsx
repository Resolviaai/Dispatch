import React from 'react';
import ReactDOM from 'react-dom/client';
import '@fontsource-variable/fraunces/full.css';
import '@fontsource-variable/manrope';
import App from './App';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
