import React from 'react';
import { Database, Layers, HardDrive, Cpu, Palette, Wifi } from 'lucide-react';
import { StatusResponse, ProjectsResponse } from '../types/api';

export type ThemeType = 'slate' | 'cyberpunk' | 'light' | 'monokai';

interface StatusFooterProps {
  status: StatusResponse | null;
  projectsData: ProjectsResponse | null;
  theme: ThemeType;
  setTheme: (theme: ThemeType) => void;
}

export const StatusFooter: React.FC<StatusFooterProps> = ({
  status,
  projectsData,
  theme,
  setTheme,
}) => {
  const activeProjName = projectsData?.active_project;
  const activeProj = activeProjName
    ? projectsData?.projects.find((p) => p.name === activeProjName)
    : undefined;

  return (
    <footer
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '8px 24px',
        backgroundColor: 'var(--bg-secondary)',
        borderTop: '1px solid var(--border-color)',
        fontSize: '11px',
        color: 'var(--text-secondary)',
        userSelect: 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Wifi size={12} color="#22c55e" />
          <span style={{ fontWeight: 600, color: 'var(--accent-green)' }}>Server Online</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <Database size={12} color="var(--accent-indigo)" />
          <span>Project:</span>
          <strong style={{ color: '#fff', fontFamily: 'var(--font-mono)' }}>{activeProjName || 'none selected'}</strong>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <Layers size={12} color="var(--accent-cyan)" />
          <span>Atoms:</span>
          <span style={{ color: '#fff' }}>{(activeProj?.atom_count ?? status?.total_atoms ?? 0).toLocaleString()}</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <HardDrive size={12} color="var(--accent-purple)" />
          <span>Trees:</span>
          <span style={{ color: '#fff' }}>{activeProj?.tree_count ?? status?.total_trees ?? 0}</span>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
        {status?.llm_model && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
            <Cpu size={12} color="var(--accent-amber)" />
            <span>LLM:</span>
            <span style={{ color: '#fff', fontFamily: 'var(--font-mono)', fontSize: '10px' }}>{status.llm_model}</span>
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Palette size={12} color="var(--text-muted)" />
          <span>Theme:</span>
          <select
            value={theme}
            onChange={(e) => setTheme(e.target.value as ThemeType)}
            style={{
              backgroundColor: 'var(--bg-tertiary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-color)',
              borderRadius: '4px',
              padding: '2px 6px',
              fontSize: '11px',
              outline: 'none',
              cursor: 'pointer',
            }}
          >
            <option value="slate">Slate Dark</option>
            <option value="cyberpunk">Cyberpunk Night</option>
            <option value="monokai">Monokai</option>
            <option value="light">Clean Light</option>
          </select>
        </div>
      </div>
    </footer>
  );
};
