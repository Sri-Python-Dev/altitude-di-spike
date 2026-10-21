// Bundled once with esbuild; served by ui/server.py at /static/jdm-editor.js.
import React from 'react';
import { createRoot } from 'react-dom/client';
import { JdmConfigProvider, DecisionGraph } from '@gorules/jdm-editor';
import '@gorules/jdm-editor/dist/style.css';

function Editor({ initial, onChange }) {
  const [value, setValue] = React.useState(initial);
  return (
    <JdmConfigProvider>
      <div style={{ height: '100%' }}>
        <DecisionGraph value={value} onChange={(v) => { setValue(v); onChange(v); }} />
      </div>
    </JdmConfigProvider>
  );
}

window.mountJdmEditor = (element, initial, onChange) => {
  createRoot(element).render(<Editor initial={initial} onChange={onChange} />);
};
