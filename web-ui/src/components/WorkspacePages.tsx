import React from 'react';
import { FileText, FolderSync, GitBranch, Inbox, ListTree, Plus, RefreshCw, ShieldAlert, Sparkles, WandSparkles } from 'lucide-react';
import { SpineResponse, StatusResponse } from '../types/api';

interface CommonProps {
  status: StatusResponse | null;
  onAddSource?: () => void;
  onOrganize: () => void;
  onRebuild: () => void;
  isOrganizing: boolean;
  isRebuilding: boolean;
}

const statusCopy = (status: StatusResponse | null) => {
  if (!status || status.total_atoms === 0) {
    return { label: 'Ready for a source', title: 'Start with something worth remembering', tone: 'neutral' };
  }
  if (status.needs_recovery) {
    return { label: 'Needs recovery', title: 'Some source content needs repair', tone: 'warning' };
  }
  if (!status.index_trusted) {
    return { label: 'Index needs reindex', title: 'Derived structure is not yet trusted', tone: 'warning' };
  }
  if (status.needs_organization) {
    return { label: 'Ready to organize', title: 'New content is waiting for structure', tone: 'pending' };
  }
  return { label: 'Ready to ask', title: 'Your workspace is organized', tone: 'ready' };
};

export const HomePage: React.FC<CommonProps> = ({
  status,
  onAddSource,
  onOrganize,
  onRebuild,
  isOrganizing,
  isRebuilding,
}) => {
  const phase = statusCopy(status);
  return (
    <section className="page-stack">
      <div className="home-hero">
        <div>
          <p className="eyebrow">Workspace overview</p>
          <h1>{phase.title}</h1>
          <p className="hero-copy">Trace-lite keeps original sources immutable, then builds a durable structure you can ask against.</p>
        </div>
        <div className={`state-card state-${phase.tone}`}>
          {phase.tone === 'warning' ? <ShieldAlert size={20} /> : phase.tone === 'pending' ? <WandSparkles size={20} /> : <Sparkles size={20} />}
          <span>{phase.label}</span>
          {status?.needs_recovery && <small>{status.orphaned_atoms} orphaned atom(s)</small>}
          {status?.needs_organization && !status.needs_recovery && <small>{status.pending_atoms} atom(s) queued</small>}
        </div>
      </div>

      <div className="metric-grid">
        <Metric label="Sources" value={status?.total_atoms ?? 0} icon={<Inbox size={17} />} detail="immutable atoms" />
        <Metric label="Trees" value={status?.total_trees ?? 0} icon={<GitBranch size={17} />} detail="organized topics" />
        <Metric label="Structure" value={status?.total_nodes ?? 0} icon={<ListTree size={17} />} detail={`${status?.active_nodes ?? 0} active nodes`} />
        <Metric label="Pending" value={status?.pending_atoms ?? 0} icon={<WandSparkles size={17} />} detail={status?.needs_recovery ? 'recovery also needed' : 'ready to organize'} />
      </div>

      <div className="home-grid">
        <div className="card action-card">
          <p className="eyebrow">Next step</p>
          {status?.needs_recovery ? (
            <>
              <h2>Repair the source index</h2>
              <p className="muted">Legacy or orphaned atoms are still safe in Spine. Organize repairs their derived structure without re-ingesting them.</p>
              <button className="primary-button" onClick={onOrganize} disabled={isOrganizing || isRebuilding}>
                <ShieldAlert size={16} /> {isOrganizing ? 'Recovering…' : 'Organize and recover'}
              </button>
            </>
          ) : status?.needs_organization ? (
            <>
              <h2>Give new content a home</h2>
              <p className="muted">Queued content is already durable. Organizing builds the retrieval structure when you are ready.</p>
              <button className="primary-button" onClick={onOrganize} disabled={isOrganizing || isRebuilding}>
                <WandSparkles size={16} /> {isOrganizing ? 'Organizing…' : 'Organize now'}
              </button>
            </>
          ) : !status?.index_trusted ? (
            <>
              <h2>Rebuild a verified index</h2>
              <p className="muted">This workspace contains legacy or unverified derived structure. Save & Verify a provider, then rebuild from immutable sources.</p>
              <button className="primary-button" onClick={onRebuild} disabled={isRebuilding || isOrganizing}>
                <RefreshCw size={16} /> {isRebuilding ? 'Rebuilding…' : 'Rebuild verified structure'}
              </button>
            </>
          ) : (
            <>
              <h2>Ask a grounded question</h2>
              <p className="muted">Search your organized evidence with hybrid, tree, or flat retrieval.</p>
              <button className="primary-button" onClick={onAddSource}>
                <Plus size={16} /> Add another source
              </button>
            </>
          )}
        </div>
        <div className="card quiet-card">
          <p className="eyebrow">Advanced</p>
          <h2>Rebuild structure</h2>
          <p className="muted">Regenerate every derived tree intentionally. Source artifacts and atoms remain untouched.</p>
          <button className="secondary-button" onClick={onRebuild} disabled={isRebuilding || isOrganizing}>
            <RefreshCw size={15} /> {isRebuilding ? 'Rebuilding…' : 'Rebuild structure'}
          </button>
        </div>
      </div>
    </section>
  );
};

const Metric: React.FC<{ label: string; value: number; icon: React.ReactNode; detail: string }> = ({ label, value, icon, detail }) => (
  <div className="metric-card card">
    <div className="metric-icon">{icon}</div>
    <div><p className="metric-label">{label}</p><strong>{value.toLocaleString()}</strong><small>{detail}</small></div>
  </div>
);

export const SourcesPage: React.FC<{ spine: SpineResponse | null; onAddSource: () => void }> = ({ spine, onAddSource }) => (
  <section className="page-stack">
    <div className="page-intro page-intro-actions">
      <div><p className="eyebrow">Sources</p><h1>Keep the originals close</h1><p className="muted">Paste, upload, or sync a folder. Changed files become visible immutable versions.</p></div>
      <button className="primary-button" onClick={onAddSource}><Plus size={16} /> Add source</button>
    </div>
    <div className="source-summary card"><FolderSync size={18} /><span>{spine?.folders?.length ?? 0} connected folder(s)</span><span className="muted">{spine?.artifacts_count ?? 0} immutable artifact(s)</span></div>
    {!spine || spine.artifacts.length === 0 ? (
      <div className="card empty-state large-empty"><Inbox size={34} /><h2>No sources yet</h2><p>Paste a note or connect a folder to create your first durable artifact.</p><button className="secondary-button" onClick={onAddSource}>Add your first source</button></div>
    ) : (
      <div className="source-list">{spine.artifacts.map((artifact) => <article className="source-row card" key={artifact.artifact_id}><div className="source-icon"><FileText size={18} /></div><div className="source-content"><h2>{artifact.document_name}</h2><p>{artifact.content_preview || 'No preview available.'}</p><small>{artifact.atom_count} atoms · {artifact.created_at ? new Date(artifact.created_at).toLocaleString() : 'unknown date'}</small></div><code>{artifact.artifact_id}</code></article>)}</div>
    )}
  </section>
);

export const OrganizePage: React.FC<CommonProps> = ({ status, onOrganize, onRebuild, isOrganizing, isRebuilding }) => (
  <section className="page-stack">
    <div className="page-intro"><div><p className="eyebrow">Organize</p><h1>Make structure when it helps</h1><p className="muted">Ingestion is durable immediately. Organization is a deliberate, inspectable step.</p></div></div>
    <div className={`organization-banner card ${status?.needs_recovery ? 'is-warning' : status?.needs_organization ? 'is-pending' : 'is-ready'}`}>
      {status?.needs_recovery ? <ShieldAlert size={24} /> : status?.needs_organization ? <WandSparkles size={24} /> : <Sparkles size={24} />}
      <div><h2>{status?.needs_recovery ? 'Needs recovery' : status?.needs_organization ? 'Ready to organize' : 'Ready to ask'}</h2><p>{status?.needs_recovery ? `${status.orphaned_atoms} orphaned atom(s) will be routed without re-ingestion.` : status?.needs_organization ? `${status.pending_atoms} source atom(s) are durably queued; routing happens during organization.` : 'There is no pending organization work.'}</p></div>
      <button className="primary-button" onClick={onOrganize} disabled={!status?.needs_organization || isOrganizing || isRebuilding}>{isOrganizing ? 'Organizing…' : status?.needs_recovery ? 'Organize and recover' : 'Organize now'}</button>
    </div>
    <div className="card advanced-card"><div><p className="eyebrow">Advanced action</p><h2>Rebuild structure</h2><p className="muted">Full rebuilds replace derived nodes and vectors atomically. Use this when you intentionally want a clean derived index.</p></div><button className="secondary-button" onClick={onRebuild} disabled={isRebuilding || isOrganizing}><ListTree size={15} /> {isRebuilding ? 'Rebuilding…' : 'Rebuild structure'}</button></div>
  </section>
);
