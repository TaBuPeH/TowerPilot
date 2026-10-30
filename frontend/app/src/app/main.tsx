import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '../ui/tokens.css';
import '../ui/base.css';
import { App } from './App.tsx';

const container = document.getElementById('root');
if (container === null) {
  throw new Error('index.html has no #root element');
}
createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
