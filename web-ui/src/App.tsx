import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Header, TabType } from './components/Header';
import { LatticeQueryDebugger } from './components/LatticeQueryDebugger';
import { RaptorForestView } from './components/RaptorForestView';
import { VectorExplorer3D } from './components/VectorExplorer3D';
import { SpineViewer } from './components/SpineViewer';
import { IngestModal } from './components/IngestModal';
import { ProjectManagerModal } from './components/ProjectManagerModal';
import { SettingsModal } from './components/SettingsModal';
import { CommandPalette } from './components/CommandPalette';
import { StatusFooter, ThemeType } from './components/StatusFooter';
import { HomePage, OrganizePage, SourcesPage } from './components/WorkspacePages';
import {
  consolidateTrees,
  fetchConfig,
  fetchModelStatus,
  fetchProjects,
  fetchSpine,
  fetchStatus,
  fetchTrees,
  fetchVectors,
  organizeSources,
} from './services/api';
import { ConfigResponse, ForestResponse, ModelStatusResponse, ProjectsResponse, SpineResponse, StatusResponse, VectorsResponse } from './types/api';

type ExploreTab = 'forest' | 'vectors' | 'spine';

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabType>('home');
  const [exploreTab, setExploreTab] = useState<ExploreTab>('forest');
  const [isLoading, setIsLoading] = useState(true);
  const [isOrganizing, setIsOrganizing] = useState(false);
  const [isRebuilding, setIsRebuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [isIngestOpen, setIsIngestOpen] = useState(false);
  const [isProjectsOpen, setIsProjectsOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isPaletteOpen, setIsPaletteOpen] = useState(false);
  const [theme, setTheme] = useState<ThemeType>('slate');

  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [forest, setForest] = useState<ForestResponse | null>(null);
  const [vectors, setVectors] = useState<VectorsResponse | null>(null);
  const [spine, setSpine] = useState<SpineResponse | null>(null);
  const [projects, setProjects] = useState<ProjectsResponse | null>(null);
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [modelStatus, setModelStatus] = useState<ModelStatusResponse | null>(null);
  // Organize and rebuild share one synchronous guard.  React state updates are
  // asynchronous, so a ref is required to close the rapid-click race.
  const buildInFlight = useRef(false);

  const loadData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    const results = await Promise.allSettled([
      fetchStatus(), fetchTrees(), fetchVectors(), fetchSpine(), fetchProjects(), fetchConfig(), fetchModelStatus(),
    ]);
    const [statusResult, forestResult, vectorsResult, spineResult, projectsResult, configResult, modelResult] = results;
    const failures = results.filter((result) => result.status === 'rejected');
    if (statusResult.status === 'fulfilled') setStatus(statusResult.value);
    if (forestResult.status === 'fulfilled') setForest(forestResult.value);
    if (vectorsResult.status === 'fulfilled') setVectors(vectorsResult.value);
    if (spineResult.status === 'fulfilled') setSpine(spineResult.value);
    if (projectsResult.status === 'fulfilled') setProjects(projectsResult.value);
    if (configResult.status === 'fulfilled') setConfig(configResult.value);
    if (modelResult.status === 'fulfilled') setModelStatus(modelResult.value);
    const noActiveProject = projectsResult.status === 'fulfilled' && projectsResult.value.active_project === null;
    if (failures.length > 0 && !noActiveProject) {
      const first = failures[0];
      setError(first.status === 'rejected' && first.reason instanceof Error ? first.reason.message : 'Some workspace data could not be loaded.');
    }
    setIsLoading(false);
  }, []);

  useEffect(() => { void loadData(); }, [loadData]);
  useEffect(() => {
    if (modelStatus?.status !== 'loading') return undefined;
    const timer = window.setInterval(() => {
      void fetchModelStatus().then(setModelStatus).catch(() => undefined);
    }, 750);
    return () => window.clearInterval(timer);
  }, [modelStatus?.status]);
  useEffect(() => { document.documentElement.setAttribute('data-theme', theme); }, [theme]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setIsPaletteOpen(true);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const showToast = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 5000);
  };

  const handleOrganize = async () => {
    if (buildInFlight.current) return;
    buildInFlight.current = true;
    setIsOrganizing(true);
    try {
      const result = await organizeSources();
      showToast(`Organized ${result.trees_updated} tree(s); ${result.pending_atoms} atom(s) remain queued.`);
      await loadData();
    } catch (err) {
      showToast(`Organization failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setIsOrganizing(false);
      buildInFlight.current = false;
    }
  };

  const handleRebuild = async () => {
    if (buildInFlight.current) return;
    buildInFlight.current = true;
    setIsRebuilding(true);
    try {
      const result = await consolidateTrees();
      showToast(`Rebuilt ${result.trees_updated} tree(s) and generated ${result.summaries_generated} summary node(s).`);
      await loadData();
    } catch (err) {
      showToast(`Rebuild failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setIsRebuilding(false);
      buildInFlight.current = false;
    }
  };

  const renderContent = () => {
    if (activeTab === 'home') return <HomePage status={status} onAddSource={() => setIsIngestOpen(true)} onOrganize={handleOrganize} onRebuild={handleRebuild} isOrganizing={isOrganizing} isRebuilding={isRebuilding} />;
    if (activeTab === 'ask') return <LatticeQueryDebugger onQueryComplete={loadData} />;
    if (activeTab === 'sources') return <SourcesPage spine={spine} onAddSource={() => setIsIngestOpen(true)} />;
    if (activeTab === 'organize') return <OrganizePage status={status} onOrganize={handleOrganize} onRebuild={handleRebuild} isOrganizing={isOrganizing} isRebuilding={isRebuilding} />;
    return (
      <section className="page-stack explore-page">
        <div className="page-intro"><div><p className="eyebrow">Explore</p><h1>See the machinery</h1><p className="muted">Advanced views stay available when you want to inspect the forest, vectors, or source ledger.</p></div></div>
        <div className="explore-tabs" role="tablist" aria-label="Advanced views">
          {(['forest', 'vectors', 'spine'] as ExploreTab[]).map((tab) => <button key={tab} className={`choice-button ${exploreTab === tab ? 'is-selected' : ''}`} onClick={() => setExploreTab(tab)} role="tab" aria-selected={exploreTab === tab}>{tab === 'forest' ? 'Forest' : tab === 'vectors' ? 'Vectors' : 'Spine'}</button>)}
        </div>
        {exploreTab === 'forest' && <RaptorForestView forest={forest} status={status} />}
        {exploreTab === 'vectors' && <VectorExplorer3D vectors={vectors} />}
        {exploreTab === 'spine' && <SpineViewer spine={spine} />}
      </section>
    );
  };

  return (
    <div className="app-shell">
      <Header activeTab={activeTab} setActiveTab={setActiveTab} onRefresh={() => void loadData()} onOpenIngestModal={() => setIsIngestOpen(true)} onOpenProjectsModal={() => setIsProjectsOpen(true)} onOpenSettingsModal={() => setIsSettingsOpen(true)} onOpenCommandPalette={() => setIsPaletteOpen(true)} onOrganize={handleOrganize} onRebuild={handleRebuild} isLoading={isLoading} isOrganizing={isOrganizing} isRebuilding={isRebuilding} status={status} projectsData={projects} />
      {toast && <div className="toast" role="status">{toast}</div>}
      {error && <div className="global-error" role="alert">{error}<button onClick={() => setError(null)} aria-label="Dismiss error">×</button></div>}
      <main className="workspace-main">{renderContent()}</main>
      <StatusFooter status={status} projectsData={projects} theme={theme} setTheme={setTheme} />
      <IngestModal isOpen={isIngestOpen} onClose={() => setIsIngestOpen(false)} onSuccess={() => void loadData()} allowServerPaths={projects?.server_paths_allowed !== false} />
      <ProjectManagerModal isOpen={isProjectsOpen} onClose={() => setIsProjectsOpen(false)} projectsData={projects} onProjectsUpdated={() => void loadData()} />
      <SettingsModal isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} configData={config} modelStatus={modelStatus} onConfigUpdated={() => void loadData()} />
      <CommandPalette isOpen={isPaletteOpen} onClose={() => setIsPaletteOpen(false)} setActiveTab={setActiveTab} onOpenIngest={() => setIsIngestOpen(true)} onOpenProjects={() => setIsProjectsOpen(true)} onOpenSettings={() => setIsSettingsOpen(true)} onOrganize={handleOrganize} onRebuild={handleRebuild} onRefresh={() => void loadData()} />
    </div>
  );
};

export default App;
