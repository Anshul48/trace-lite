import React, { useState } from 'react';
import { executeQuery } from '../services/api';
import { QueryResponse, QueryEvidenceItem } from '../types/api';
import {
  Terminal,
  Search,
  Play,
  FileText,
  AlertCircle,
  ArrowRight,
} from 'lucide-react';

export const LatticeQueryDebugger: React.FC = () => {
  const [queryText, setQueryText] = useState('How does RAPTOR clustering summarize context?');
  const [mode, setMode] = useState<'hybrid' | 'vector' | 'bm25' | 'raptor'>('hybrid');
  const [topK, setTopK] = useState<number>(5);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [queryResult, setQueryResult] = useState<QueryResponse | null>(null);
  const [queryHistory, setQueryHistory] = useState<QueryResponse[]>([]);

  const handleRunQuery = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!queryText.trim()) return;

    setIsLoading(true);
    setError(null);
    try {
      const res = await executeQuery(queryText, topK, mode);
      setQueryResult(res);
      setQueryHistory((prev) => [res, ...prev.slice(0, 4)]);
    } catch (err: any) {
      setError(err.message || 'Failed to execute query');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* Query Bar Control */}
      <div className="glass-card" style={{ padding: '20px' }}>
        <form onSubmit={handleRunQuery} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <h3 style={{ fontSize: '15px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Terminal size={18} color="var(--accent-green)" />
              LATTICE Retrieval Console
            </h3>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              Interactive query execution & traversal path inspection
            </span>
          </div>

          <div style={{ display: 'flex', gap: '12px' }}>
            <div style={{ flex: 1, position: 'relative' }}>
              <Search
                size={16}
                style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }}
              />
              <input
                type="text"
                value={queryText}
                onChange={(e) => setQueryText(e.target.value)}
                placeholder="Enter query to trace retrieval path..."
                style={{ width: '100%', paddingLeft: '38px', height: '42px', fontSize: '14px' }}
              />
            </div>

            <button
              type="submit"
              disabled={isLoading || !queryText.trim()}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '0 20px',
                borderRadius: 'var(--radius-sm)',
                backgroundColor: 'var(--accent-green)',
                color: '#0a0d14',
                fontWeight: 700,
                fontSize: '14px',
                opacity: isLoading ? 0.6 : 1,
              }}
            >
              <Play size={16} fill="#0a0d14" />
              {isLoading ? 'Retrieving...' : 'Run Query'}
            </button>
          </div>

          {/* Controls line */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '24px', flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontWeight: 600 }}>SEARCH MODE:</span>
              {(['hybrid', 'vector', 'bm25', 'raptor'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  style={{
                    padding: '4px 10px',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '12px',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    backgroundColor: mode === m ? 'var(--accent-green)' : 'var(--bg-secondary)',
                    color: mode === m ? '#0a0d14' : 'var(--text-secondary)',
                    border: '1px solid var(--border-color)',
                  }}
                >
                  {m}
                </button>
              ))}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontWeight: 600 }}>TOP K:</span>
              <select
                value={topK}
                onChange={(e) => setTopK(Number(e.target.value))}
                style={{ padding: '4px 10px', fontSize: '12px', fontWeight: 600 }}
              >
                {[3, 5, 8, 10, 15, 20].map((k) => (
                  <option key={k} value={k}>
                    {k} items
                  </option>
                ))}
              </select>
            </div>
          </div>
        </form>
      </div>

      {error && (
        <div
          style={{
            padding: '12px 16px',
            backgroundColor: 'rgba(244, 63, 94, 0.15)',
            border: '1px solid var(--accent-rose)',
            borderRadius: 'var(--radius-sm)',
            color: 'var(--accent-rose)',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            fontSize: '13px',
          }}
        >
          <AlertCircle size={16} />
          {error}
        </div>
      )}

      {/* Results View & History Split */}
      <div style={{ display: 'grid', gridTemplateColumns: queryHistory.length > 0 ? '1fr 280px' : '1fr', gap: '20px' }}>
        {/* Active Query Output */}
        <div className="glass-card" style={{ padding: '20px' }}>
          {queryResult ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border-color)', paddingBottom: '12px' }}>
                <div>
                  <h4 style={{ fontSize: '15px', fontWeight: 700 }}>Query Evidence Results</h4>
                  <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                    Found {queryResult.results_count} evidence atoms using mode <span style={{ color: 'var(--accent-green)', fontWeight: 600 }}>{queryResult.mode}</span>
                  </p>
                </div>
                <span
                  style={{
                    fontSize: '11px',
                    fontFamily: 'var(--font-mono)',
                    padding: '4px 8px',
                    borderRadius: '4px',
                    backgroundColor: 'rgba(52, 211, 153, 0.15)',
                    color: 'var(--accent-green)',
                    border: '1px solid rgba(52, 211, 153, 0.3)',
                  }}
                >
                  Query Executed
                </span>
              </div>

              {/* Items List */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                {queryResult.items.map((item: QueryEvidenceItem, idx: number) => (
                  <div
                    key={item.atom_id + idx}
                    className="glass-card-interactive"
                    style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span
                          style={{
                            fontSize: '12px',
                            fontWeight: 700,
                            width: '24px',
                            height: '24px',
                            borderRadius: '50%',
                            backgroundColor: 'var(--accent-green)',
                            color: '#0a0d14',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                          }}
                        >
                          #{idx + 1}
                        </span>
                        <span style={{ fontSize: '12px', fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--accent-cyan)' }}>
                          Atom: {item.atom_id}
                        </span>
                        <span style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                          <FileText size={12} /> {item.source}
                        </span>
                      </div>

                      <div
                        style={{
                          fontSize: '12px',
                          fontWeight: 700,
                          fontFamily: 'var(--font-mono)',
                          color: 'var(--accent-amber)',
                          backgroundColor: 'rgba(251, 191, 36, 0.15)',
                          padding: '2px 8px',
                          borderRadius: '12px',
                          border: '1px solid rgba(251, 191, 36, 0.3)',
                        }}
                      >
                        Score: {item.score}
                      </div>
                    </div>

                    {/* Content Preview */}
                    <div
                      style={{
                        fontSize: '13px',
                        lineHeight: '1.5',
                        padding: '12px',
                        backgroundColor: 'var(--bg-secondary)',
                        borderRadius: 'var(--radius-sm)',
                        border: '1px solid var(--border-color)',
                      }}
                    >
                      {item.content}
                    </div>

                    {/* Traversal Path Trajectory */}
                    {item.traversal_path && item.traversal_path.length > 0 && (
                      <div>
                        <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '6px', fontWeight: 600 }}>
                          TRAVERSAL TRAJECTORY PATH:
                        </div>
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '6px',
                            flexWrap: 'wrap',
                            fontSize: '11px',
                            fontFamily: 'var(--font-mono)',
                          }}
                        >
                          {item.traversal_path.map((step: string, sIdx: number) => (
                            <React.Fragment key={sIdx}>
                              {sIdx > 0 && <ArrowRight size={12} color="var(--accent-indigo)" />}
                              <span
                                style={{
                                  padding: '2px 8px',
                                  borderRadius: '4px',
                                  backgroundColor: sIdx === item.traversal_path!.length - 1 ? 'rgba(56, 189, 248, 0.2)' : 'var(--bg-tertiary)',
                                  color: sIdx === item.traversal_path!.length - 1 ? 'var(--accent-cyan)' : 'var(--text-secondary)',
                                  border: '1px solid var(--border-color)',
                                }}
                              >
                                {step}
                              </span>
                            </React.Fragment>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
              <Terminal size={40} style={{ marginBottom: '12px', opacity: 0.5 }} />
              <h4>Ready for Retrieval Testing</h4>
              <p style={{ fontSize: '13px' }}>
                Enter a search query above and click "Run Query" to debug LATTICE trajectory paths.
              </p>
            </div>
          )}
        </div>

        {/* Query Session History */}
        {queryHistory.length > 0 && (
          <div className="glass-card" style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <h4 style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              Recent Session Queries
            </h4>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {queryHistory.map((h, i) => (
                <div
                  key={i}
                  onClick={() => {
                    setQueryText(h.query_text);
                    setQueryResult(h);
                  }}
                  className="glass-card-interactive"
                  style={{ padding: '10px', cursor: 'pointer' }}
                >
                  <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '4px' }}>
                    {h.query_text}
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', color: 'var(--text-muted)' }}>
                    <span>Mode: {h.mode}</span>
                    <span>{h.results_count} results</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
