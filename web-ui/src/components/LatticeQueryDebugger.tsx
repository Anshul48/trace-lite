import React, { useState } from 'react';
import { AlertCircle, ArrowRight, FileText, Search, SlidersHorizontal, Sparkles } from 'lucide-react';
import { executeQuery } from '../services/api';
import { QueryResponse } from '../types/api';

interface Props {
  onQueryComplete?: () => void;
}

export const LatticeQueryDebugger: React.FC<Props> = ({ onQueryComplete }) => {
  const [queryText, setQueryText] = useState('');
  const [mode, setMode] = useState<'hybrid' | 'tree' | 'flat'>('hybrid');
  const [topK, setTopK] = useState(5);
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [queryResult, setQueryResult] = useState<QueryResponse | null>(null);

  const runQuery = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!queryText.trim()) return;
    setIsLoading(true);
    setError(null);
    try {
      const result = await executeQuery(queryText.trim(), topK, mode);
      setQueryResult(result);
      onQueryComplete?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to ask the workspace.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <section className="page-stack ask-page">
      <div className="page-intro">
        <div>
          <p className="eyebrow">Ask</p>
          <h1>Ask your sources</h1>
          <p className="muted">Answers begin with cited evidence from your workspace. Choose a retrieval mode only when you need to.</p>
        </div>
        <span className="soft-pill"><Sparkles size={14} /> source-grounded</span>
      </div>

      <form className="ask-form card" onSubmit={runQuery}>
        <label htmlFor="workspace-query" className="sr-only">Question</label>
        <div className="query-row">
          <Search size={18} className="query-icon" aria-hidden="true" />
          <input
            id="workspace-query"
            value={queryText}
            onChange={(event) => setQueryText(event.target.value)}
            placeholder="What would you like to understand?"
            autoComplete="off"
          />
          <button className="primary-button" type="submit" disabled={isLoading || !queryText.trim()}>
            {isLoading ? 'Searching…' : 'Ask'}
          </button>
        </div>
        <div className="ask-options">
          <div className="mode-picker" aria-label="Retrieval mode">
            <span className="option-label"><SlidersHorizontal size={14} /> Mode</span>
            {(['hybrid', 'tree', 'flat'] as const).map((option) => (
              <button
                type="button"
                key={option}
                className={`choice-button ${mode === option ? 'is-selected' : ''}`}
                onClick={() => setMode(option)}
                aria-pressed={mode === option}
              >
                {option}
              </button>
            ))}
          </div>
          <label className="top-k-control">
            <span className="option-label">Evidence</span>
            <select value={topK} onChange={(event) => setTopK(Number(event.target.value))}>
              {[3, 5, 8, 10].map((value) => <option key={value} value={value}>{value} passages</option>)}
            </select>
          </label>
          <label className="diagnostic-toggle">
            <input type="checkbox" checked={showDiagnostics} onChange={(event) => setShowDiagnostics(event.target.checked)} />
            Show traversal diagnostics
          </label>
        </div>
      </form>

      {error && <div className="error-state" role="alert"><AlertCircle size={17} /> {error}</div>}

      <div className="evidence-panel card">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Evidence first</p>
            <h2>{queryResult ? `${queryResult.results_count} passages found` : 'Your answer will appear here'}</h2>
          </div>
          {queryResult && <span className="soft-pill">{queryResult.mode} retrieval</span>}
        </div>
        {!queryResult && <div className="empty-state"><Search size={28} /><p>Ask a question to search the organized source index.</p></div>}
        {queryResult && queryResult.items.length === 0 && (
          <div className="empty-state"><Search size={28} /><p>No matching evidence found. Try a broader question or another mode.</p></div>
        )}
        <div className="evidence-list">
          {queryResult?.items.map((item, index) => (
            <article className="evidence-item" key={`${item.atom_id}-${index}`}>
              <div className="evidence-meta">
                <span className="evidence-rank">{index + 1}</span>
                <span><FileText size={13} /> {item.source || 'Source'}</span>
                <span className="score">{item.score.toFixed(3)}</span>
              </div>
              <p>{item.content}</p>
              {showDiagnostics && item.traversal_path && item.traversal_path.length > 0 && (
                <div className="diagnostics">
                  <span className="option-label">Traversal</span>
                  {item.traversal_path.map((step, stepIndex) => (
                    <React.Fragment key={`${step}-${stepIndex}`}>
                      {stepIndex > 0 && <ArrowRight size={12} />}
                      <code>{step}</code>
                    </React.Fragment>
                  ))}
                </div>
              )}
            </article>
          ))}
        </div>
      </div>
    </section>
  );
};
