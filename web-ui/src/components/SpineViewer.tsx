import React from 'react';
import { SpineResponse } from '../types/api';
import { Database, FileText, Activity, Clock } from 'lucide-react';

interface SpineViewerProps {
  spine: SpineResponse | null;
}

export const SpineViewer: React.FC<SpineViewerProps> = ({ spine }) => {
  if (!spine) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
        <Database size={48} style={{ marginBottom: '12px', opacity: 0.5 }} />
        <h3>No Spine Data Loaded</h3>
      </div>
    );
  }

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '24px' }}>
      {/* Spine Artifacts Table */}
      <div className="glass-card" style={{ padding: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h3 style={{ fontSize: '15px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '8px' }}>
            <FileText size={18} color="var(--accent-amber)" />
            Spine Document Artifacts ({spine.artifacts_count})
          </h3>
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            Stored document records & parsed semantic atom counts
          </span>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '16px' }}>
          {spine.artifacts.map((art) => (
            <div key={art.artifact_id} className="glass-card-interactive" style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <span style={{ fontSize: '14px', fontWeight: 700, color: 'var(--accent-cyan)' }}>
                  {art.document_name}
                </span>
                <span
                  style={{
                    fontSize: '11px',
                    fontFamily: 'var(--font-mono)',
                    padding: '2px 8px',
                    borderRadius: '12px',
                    backgroundColor: 'rgba(251, 191, 36, 0.15)',
                    color: 'var(--accent-amber)',
                    border: '1px solid rgba(251, 191, 36, 0.3)',
                  }}
                >
                  {art.atom_count} atoms
                </span>
              </div>

              <div style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                ID: {art.artifact_id}
              </div>

              <div
                style={{
                  fontSize: '12px',
                  color: 'var(--text-secondary)',
                  backgroundColor: 'var(--bg-secondary)',
                  padding: '8px',
                  borderRadius: 'var(--radius-sm)',
                  maxHeight: '80px',
                  overflow: 'hidden',
                }}
              >
                {art.content_preview}
              </div>

              <div style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '4px', marginTop: 'auto' }}>
                <Clock size={12} />
                Created: {art.created_at ? new Date(art.created_at).toLocaleString() : 'N/A'}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Event Stream Log */}
      <div className="glass-card" style={{ padding: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h3 style={{ fontSize: '15px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Activity size={18} color="var(--accent-purple)" />
            Cortex Event Log Stream ({spine.events.length})
          </h3>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {spine.events.map((evt) => (
            <div
              key={evt.event_id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '10px 14px',
                backgroundColor: 'var(--bg-secondary)',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-color)',
                fontFamily: 'var(--font-mono)',
                fontSize: '12px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <span style={{ color: 'var(--accent-purple)', fontWeight: 600 }}>{evt.event_type}</span>
                <span style={{ color: 'var(--text-secondary)' }}>
                  {JSON.stringify(evt.payload)}
                </span>
              </div>
              <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>
                {evt.timestamp ? new Date(evt.timestamp).toLocaleTimeString() : 'N/A'}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
