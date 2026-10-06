import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { LanguageBootstrap } from './LanguageSettings';
import './styles.css';
import './Workbench.css';
import './SecondaryPages.css';
createRoot(document.getElementById('root')!).render(<StrictMode><LanguageBootstrap /><App /></StrictMode>);
