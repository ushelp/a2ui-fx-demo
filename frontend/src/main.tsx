import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
// A2UI basic catalog 的 Icon 组件渲染 <i class="material-icons">，需要该字体才能显示图标
import 'material-icons/iconfont/filled.css';
import App from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
