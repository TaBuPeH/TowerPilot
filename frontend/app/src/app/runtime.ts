import { createElement, useEffect, useState } from 'react';
import { createRuntimeReader } from '../api/runtime.ts';
import { queryFromHash } from '../core/runtime.ts';
import { RuntimePage } from '../features/runtime/RuntimePage.tsx';
import { createRuntimeStore } from '../store/runtime.ts';

export function ConnectedRuntimePage() {
  const [runtime] = useState(() =>
    createRuntimeStore(createRuntimeReader(), queryFromHash(window.location.hash)),
  );
  useEffect(() => {
    runtime.start();
    const restore = () => {
      if (/^#\/?runtime(?:\?|$)/.test(window.location.hash))
        runtime.restore(queryFromHash(window.location.hash));
    };
    window.addEventListener('hashchange', restore);
    return () => {
      window.removeEventListener('hashchange', restore);
      runtime.dispose();
    };
  }, [runtime]);
  return createElement(RuntimePage, { runtime });
}
