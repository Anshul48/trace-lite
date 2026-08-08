import React from 'react';
import { StatusResponse } from '../types/api';
import { Layers, GitFork, Cpu, Activity, HardDrive, Sparkles } from 'lucide-react';

interface StatusBannerProps {
  status: StatusResponse | null;
}

export const StatusBanner: React.FC<StatusBannerProps> = ({ status }) => {
  if (!status) return null;

  return (
    <div
      style={{
        padding: '12px 24px',
        backgroundColor: 'rgba(18, 24, 36, 0.8)',
        borderBottom: '1px solid var(--border-color)',
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
        gap: '12px',
      }}
    >
      <div className="glass-card" style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
        <Layers size={18} color="var(--accent-cyan)" />
        <div>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Total Atoms</div>
          <div style={{ fontSize: '15px', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{status.total_atoms}</div>
        </div>
      </div>

      <div className="glass-card" style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
        <GitFork size={18} color="var(--accent-purple)" />
        <div>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>RAPTOR Trees</div>
          <div style={{ fontSize: '15px', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{status.total_trees}</div>
        </div>
      </div>

      <div className="glass-card" style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
        <Activity size={18} color="var(--accent-green)" />
        <div>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Active Nodes</div>
          <div style={{ fontSize: '15px', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
            {status.active_nodes} <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontWeight: 400 }}>/ {status.total_nodes}</span>
          </div>
        </div>
      </div>

      <div className="glass-card" style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
        <Sparkles size={18} color="var(--accent-amber)" />
        <div>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Est. Tokens</div>
          <div style={{ fontSize: '15px', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{status.estimated_tokens.toLocaleString()}</div>
        </div>
      </div>

      <div className="glass-card" style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
        <Cpu size={18} color="var(--accent-indigo)" />
        <div>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Models</div>
          <div style={{ fontSize: '12px', fontWeight: 600, fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '140px' }}>
            {status.embedding_model}
          </div>
        </div>
      </div>

      <div className="glass-card" style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: '10px' }}>
        <HardDrive size={18} color="var(--text-secondary)" />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Data Store</div>
          <div style={{ fontSize: '11px', color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={status.data_dir}>
            {status.data_dir}
          </div>
        </div>
      </div>
    </div>
  );
};
