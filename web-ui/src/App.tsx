import React, { useState, useEffect, useCallback } from 'react';
import { Header, TabType } from './components/Header';
import { StatusBanner } from './components/StatusBanner';
import { RaptorForestView } from './components/RaptorForestView';
import { VectorExplorer3D } from './components/VectorExplorer3D';
import { LatticeQueryDebugger } from './components/LatticeQueryDebugger';
import { SpineViewer } from './components/SpineViewer';
import {
  fetchStatus,
  fetchTrees,
  fetchVectors,
  fetchSpine,
} from './services/api';
import {
  StatusResponse,
  ForestResponse,
  VectorsResponse,
  SpineResponse,
} from './types/api';

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabType>('forest');
  const [isLoading, setIsLoading] = useState<boolean>(true);

  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [forest, setForest] = useState<ForestResponse | null>(null);
  const [vectors, setVectors] = useState<VectorsResponse | null>(null);
  const [spine, setSpine] = useState<SpineResponse | null>(null);

  const loadData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [statusRes, forestRes, vectorsRes, spineRes] = await Promise.all([
        fetchStatus(),
        fetchTrees(),
        fetchVectors(),
        fetchSpine(),
      ]);
      setStatus(statusRes);
      setForest(forestRes);
      setVectors(vectorsRes);
      setSpine(spineRes);
    } catch (err) {
      console.error('Error loading dashboard data', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', backgroundColor: 'var(--bg-primary)' }}>
      {/* Header Navigation */}
      <Header
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onRefresh={loadData}
        isLoading={isLoading}
      />

      {/* Top Status & Telemetry Banner */}
      <StatusBanner status={status} />

      {/* Main Tab Workspace Views */}
      <main style={{ flex: 1 }}>
        {activeTab === 'forest' && <RaptorForestView forest={forest} />}
        {activeTab === 'vectors' && <VectorExplorer3D vectors={vectors} />}
        {activeTab === 'lattice' && <LatticeQueryDebugger />}
        {activeTab === 'spine' && <SpineViewer spine={spine} />}
      </main>
    </div>
  );
};

export default App;
