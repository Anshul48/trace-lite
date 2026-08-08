import React from 'react';
import {
  Network,
  Box,
  Terminal,
  Database,
  RefreshCw,
  Zap,
} from 'lucide-react';

export type TabType = 'forest' | 'vectors' | 'lattice' | 'spine';

interface HeaderProps {
  activeTab: TabType;
  setActiveTab: (tab: TabType) => void;
  onRefresh: () => void;
  isLoading: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  setActiveTab,
  onRefresh,
  isLoading,
}) => {
  return (
    <header
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '12px 24px',
        backgroundColor: 'var(--bg-secondary)',
        borderBottom: '1px solid var(--border-color)',
        position: 'sticky',
        top: 0,
        zIndex: 100,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            cursor: 'pointer',
          }}
          onClick={() => setActiveTab('forest')}
        >
          <div
            style={{
              width: '32px',
              height: '32px',
              borderRadius: '8px',
              background: 'linear-gradient(135deg, var(--accent-indigo), var(--accent-purple))',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 0 12px rgba(99, 102, 241, 0.4)',
            }}
          >
            <Zap size={20} color="#fff" />
          </div>
          <div>
            <div style={{ fontWeight: 700, fontSize: '16px', letterSpacing: '-0.3px', display: 'flex', alignItems: 'center', gap: '6px' }}>
              trace-lite
              <span
                style={{
                  fontSize: '10px',
                  fontWeight: 600,
                  padding: '2px 6px',
                  borderRadius: '4px',
                  backgroundColor: 'rgba(99, 102, 241, 0.2)',
                  color: 'var(--accent-indigo)',
                  border: '1px solid rgba(99, 102, 241, 0.4)',
                  fontFamily: 'var(--font-mono)',
                }}
              >
                CORTEX v0.1
              </span>
            </div>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              Developer Visualizer & Query Debugger
            </div>
          </div>
        </div>

        {/* Tab Navigation */}
        <nav style={{ display: 'flex', gap: '6px', marginLeft: '24px' }}>
          <button
            onClick={() => setActiveTab('forest')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 14px',
              borderRadius: 'var(--radius-sm)',
              fontSize: '13px',
              fontWeight: 500,
              backgroundColor: activeTab === 'forest' ? 'var(--bg-tertiary)' : 'transparent',
              color: activeTab === 'forest' ? 'var(--accent-cyan)' : 'var(--text-secondary)',
              border: activeTab === 'forest' ? '1px solid var(--border-active)' : '1px solid transparent',
            }}
          >
            <Network size={16} />
            RAPTOR Forest DAG
          </button>

          <button
            onClick={() => setActiveTab('vectors')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 14px',
              borderRadius: 'var(--radius-sm)',
              fontSize: '13px',
              fontWeight: 500,
              backgroundColor: activeTab === 'vectors' ? 'var(--bg-tertiary)' : 'transparent',
              color: activeTab === 'vectors' ? 'var(--accent-purple)' : 'var(--text-secondary)',
              border: activeTab === 'vectors' ? '1px solid var(--border-active)' : '1px solid transparent',
            }}
          >
            <Box size={16} />
            3D Vector Explorer
          </button>

          <button
            onClick={() => setActiveTab('lattice')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 14px',
              borderRadius: 'var(--radius-sm)',
              fontSize: '13px',
              fontWeight: 500,
              backgroundColor: activeTab === 'lattice' ? 'var(--bg-tertiary)' : 'transparent',
              color: activeTab === 'lattice' ? 'var(--accent-green)' : 'var(--text-secondary)',
              border: activeTab === 'lattice' ? '1px solid var(--border-active)' : '1px solid transparent',
            }}
          >
            <Terminal size={16} />
            LATTICE Query Debugger
          </button>

          <button
            onClick={() => setActiveTab('spine')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 14px',
              borderRadius: 'var(--radius-sm)',
              fontSize: '13px',
              fontWeight: 500,
              backgroundColor: activeTab === 'spine' ? 'var(--bg-tertiary)' : 'transparent',
              color: activeTab === 'spine' ? 'var(--accent-amber)' : 'var(--text-secondary)',
              border: activeTab === 'spine' ? '1px solid var(--border-active)' : '1px solid transparent',
            }}
          >
            <Database size={16} />
            Spine Artifacts
          </button>
        </nav>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <button
          onClick={onRefresh}
          disabled={isLoading}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '8px 12px',
            borderRadius: 'var(--radius-sm)',
            backgroundColor: 'var(--bg-tertiary)',
            border: '1px solid var(--border-color)',
            fontSize: '12px',
            color: 'var(--text-secondary)',
            opacity: isLoading ? 0.6 : 1,
          }}
          title="Refresh Data"
        >
          <RefreshCw size={14} className={isLoading ? 'spin' : ''} />
          {isLoading ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>
    </header>
  );
};
