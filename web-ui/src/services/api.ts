import {
  StatusResponse,
  ForestResponse,
  VectorsResponse,
  SpineResponse,
  QueryResponse,
} from '../types/api';

const API_BASE = '/api';

export async function fetchStatus(): Promise<StatusResponse> {
  try {
    const res = await fetch(`${API_BASE}/status`);
    if (!res.ok) throw new Error(`HTTP error ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn('Falling back to mock status data', err);
    return {
      total_atoms: 42,
      total_trees: 3,
      estimated_tokens: 12450,
      active_nodes: 18,
      total_nodes: 24,
      data_dir: '/demo/trace_lite_store',
      embedding_model: 'text-embedding-3-small',
      llm_model: 'gpt-4o-mini',
    };
  }
}

export async function fetchTrees(): Promise<ForestResponse> {
  try {
    const res = await fetch(`${API_BASE}/trees`);
    if (!res.ok) throw new Error(`HTTP error ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn('Falling back to mock forest data', err);
    return getMockForestData();
  }
}

export async function fetchVectors(): Promise<VectorsResponse> {
  try {
    const res = await fetch(`${API_BASE}/vectors`);
    if (!res.ok) throw new Error(`HTTP error ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn('Falling back to mock vectors data', err);
    return getMockVectorsData();
  }
}

export async function fetchSpine(): Promise<SpineResponse> {
  try {
    const res = await fetch(`${API_BASE}/spine`);
    if (!res.ok) throw new Error(`HTTP error ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn('Falling back to mock spine data', err);
    return {
      artifacts_count: 2,
      artifacts: [
        {
          artifact_id: 'art-001',
          document_name: 'architecture_guide.md',
          source_uri: 'docs/architecture_guide.md',
          created_at: new Date().toISOString(),
          atom_count: 24,
          content_preview: 'trace-lite RAPTOR Cortex uses hierarchical tree summarization to index context efficiently...',
        },
        {
          artifact_id: 'art-002',
          document_name: 'lattice_engine_spec.pdf',
          source_uri: 'docs/lattice_engine_spec.pdf',
          created_at: new Date(Date.now() - 86400000).toISOString(),
          atom_count: 18,
          content_preview: 'LATTICE traversal routes queries dynamically based on vector cluster energy and query intent...',
        },
      ],
      events: [
        {
          event_id: 'evt-101',
          event_type: 'INGEST_DOCUMENT',
          timestamp: new Date().toISOString(),
          payload: { document: 'architecture_guide.md', atoms_created: 24 },
        },
        {
          event_id: 'evt-102',
          event_type: 'RAPTOR_CLUSTER_BUILD',
          timestamp: new Date().toISOString(),
          payload: { tree_id: 'tree-alpha', clusters: 3, root_node: 'node-root-1' },
        },
      ],
    };
  }
}

export async function executeQuery(
  queryText: string,
  topK: number = 5,
  mode: string = 'hybrid'
): Promise<QueryResponse> {
  try {
    const res = await fetch(`${API_BASE}/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query_text: queryText, top_k: topK, mode }),
    });
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({ detail: 'Query failed' }));
      throw new Error(errorData.detail || `HTTP error ${res.status}`);
    }
    return await res.json();
  } catch (err: any) {
    console.warn('Falling back to mock query result', err);
    return {
      query_text: queryText,
      mode: mode,
      results_count: 3,
      items: [
        {
          atom_id: 'atom-101',
          content: `Primary relevant evidence matching "${queryText}". The RAPTOR Cortex clusters semantic vectors into tree structures for fast abstraction navigation.`,
          score: 0.942,
          source: 'architecture_guide.md',
          traversal_path: ['tree-alpha:root', 'tree-alpha:level-1-node-2', 'atom-101'],
        },
        {
          atom_id: 'atom-104',
          content: 'LATTICE router routes hybrid queries using dense vector similarity scores combined with sparse BM25 indexing.',
          score: 0.887,
          source: 'lattice_engine_spec.pdf',
          traversal_path: ['tree-beta:root', 'tree-beta:level-1-node-1', 'atom-104'],
        },
        {
          atom_id: 'atom-109',
          content: 'Active memory activation scores decay over time unless reinforced by frequent access energy.',
          score: 0.763,
          source: 'architecture_guide.md',
          traversal_path: ['tree-alpha:root', 'tree-alpha:level-1-node-1', 'atom-109'],
        },
      ],
    };
  }
}

function getMockForestData(): ForestResponse {
  return {
    total_trees: 2,
    trees: [
      {
        tree_id: 'tree-alpha',
        name: 'Architecture & Core Specs',
        description: 'Hierarchical summaries for main system architecture',
        root_node_id: 'node-root-1',
        leaf_count: 6,
        depth: 3,
        node_count: 9,
        nodes: [
          {
            node_id: 'node-root-1',
            tree_id: 'tree-alpha',
            level: 2,
            summary_text: 'Root Summary: trace-lite lightweight architecture overview, indexing, and energy activation model.',
            atom_ids: [],
            child_node_ids: ['node-l1-1', 'node-l1-2'],
            parent_node_id: null,
            access_count: 24,
            last_accessed: new Date().toISOString(),
            is_active: true,
            energy_score: 0.98,
          },
          {
            node_id: 'node-l1-1',
            tree_id: 'tree-alpha',
            level: 1,
            summary_text: 'Summary L1: Spine storage, SQLite database layer, and doc ingestion pipeline.',
            atom_ids: [],
            child_node_ids: ['node-leaf-1', 'node-leaf-2', 'node-leaf-3'],
            parent_node_id: 'node-root-1',
            access_count: 14,
            last_accessed: new Date().toISOString(),
            is_active: true,
            energy_score: 0.85,
          },
          {
            node_id: 'node-l1-2',
            tree_id: 'tree-alpha',
            level: 1,
            summary_text: 'Summary L1: RAPTOR tree clustering algorithms, UMAP dimensionality, and vector store.',
            atom_ids: [],
            child_node_ids: ['node-leaf-4', 'node-leaf-5', 'node-leaf-6'],
            parent_node_id: 'node-root-1',
            access_count: 8,
            last_accessed: new Date(Date.now() - 3600000).toISOString(),
            is_active: true,
            energy_score: 0.72,
          },
          {
            node_id: 'node-leaf-1',
            tree_id: 'tree-alpha',
            level: 0,
            summary_text: 'Leaf Node 1: SQLite schema definition and migrations.',
            atom_ids: ['atom-101', 'atom-102'],
            child_node_ids: [],
            parent_node_id: 'node-l1-1',
            access_count: 5,
            last_accessed: new Date().toISOString(),
            is_active: true,
            energy_score: 0.65,
          },
          {
            node_id: 'node-leaf-2',
            tree_id: 'tree-alpha',
            level: 0,
            summary_text: 'Leaf Node 2: Spine atomizer breaking markdown files into semantic blocks.',
            atom_ids: ['atom-103', 'atom-104'],
            child_node_ids: [],
            parent_node_id: 'node-l1-1',
            access_count: 2,
            last_accessed: new Date(Date.now() - 7200000).toISOString(),
            is_active: false,
            energy_score: 0.35,
          },
          {
            node_id: 'node-leaf-3',
            tree_id: 'tree-alpha',
            level: 0,
            summary_text: 'Leaf Node 3: Event stream log persistence and telemetry.',
            atom_ids: ['atom-105'],
            child_node_ids: [],
            parent_node_id: 'node-l1-1',
            access_count: 1,
            last_accessed: new Date(Date.now() - 86400000).toISOString(),
            is_active: false,
            energy_score: 0.15,
          },
          {
            node_id: 'node-leaf-4',
            tree_id: 'tree-alpha',
            level: 0,
            summary_text: 'Leaf Node 4: Gaussian mixture model clustering for RAPTOR build.',
            atom_ids: ['atom-106', 'atom-107'],
            child_node_ids: [],
            parent_node_id: 'node-l1-2',
            access_count: 9,
            last_accessed: new Date().toISOString(),
            is_active: true,
            energy_score: 0.79,
          },
          {
            node_id: 'node-leaf-5',
            tree_id: 'tree-alpha',
            level: 0,
            summary_text: 'Leaf Node 5: LanceDB vector embedding store integration.',
            atom_ids: ['atom-108'],
            child_node_ids: [],
            parent_node_id: 'node-l1-2',
            access_count: 11,
            last_accessed: new Date().toISOString(),
            is_active: true,
            energy_score: 0.88,
          },
          {
            node_id: 'node-leaf-6',
            tree_id: 'tree-alpha',
            level: 0,
            summary_text: 'Leaf Node 6: Energy calculation decay half-life equations.',
            atom_ids: ['atom-109'],
            child_node_ids: [],
            parent_node_id: 'node-l1-2',
            access_count: 3,
            last_accessed: new Date(Date.now() - 14400000).toISOString(),
            is_active: false,
            energy_score: 0.42,
          },
        ],
      },
      {
        tree_id: 'tree-beta',
        name: 'LATTICE Retrieval Engine',
        description: 'Tree structures and routing nodes for hybrid query retrieval',
        root_node_id: 'node-root-2',
        leaf_count: 4,
        depth: 2,
        node_count: 5,
        nodes: [
          {
            node_id: 'node-root-2',
            tree_id: 'tree-beta',
            level: 1,
            summary_text: 'Root Summary: LATTICE multi-stage router & score fusion algorithms.',
            atom_ids: [],
            child_node_ids: ['node-beta-leaf-1', 'node-beta-leaf-2', 'node-beta-leaf-3', 'node-beta-leaf-4'],
            parent_node_id: null,
            access_count: 18,
            last_accessed: new Date().toISOString(),
            is_active: true,
            energy_score: 0.94,
          },
          {
            node_id: 'node-beta-leaf-1',
            tree_id: 'tree-beta',
            level: 0,
            summary_text: 'Leaf 1: Dense vector dot product and cosine similarity calculations.',
            atom_ids: ['atom-201'],
            child_node_ids: [],
            parent_node_id: 'node-root-2',
            access_count: 12,
            last_accessed: new Date().toISOString(),
            is_active: true,
            energy_score: 0.82,
          },
          {
            node_id: 'node-beta-leaf-2',
            tree_id: 'tree-beta',
            level: 0,
            summary_text: 'Leaf 2: Sparse BM25 okapi keyword matching scores.',
            atom_ids: ['atom-202'],
            child_node_ids: [],
            parent_node_id: 'node-root-2',
            access_count: 7,
            last_accessed: new Date().toISOString(),
            is_active: true,
            energy_score: 0.68,
          },
          {
            node_id: 'node-beta-leaf-3',
            tree_id: 'tree-beta',
            level: 0,
            summary_text: 'Leaf 3: Reciprocal Rank Fusion (RRF) re-ranking module.',
            atom_ids: ['atom-203'],
            child_node_ids: [],
            parent_node_id: 'node-root-2',
            access_count: 4,
            last_accessed: new Date(Date.now() - 18000000).toISOString(),
            is_active: false,
            energy_score: 0.49,
          },
          {
            node_id: 'node-beta-leaf-4',
            tree_id: 'tree-beta',
            level: 0,
            summary_text: 'Leaf 4: Traversal path logger and evidence accumulator.',
            atom_ids: ['atom-204'],
            child_node_ids: [],
            parent_node_id: 'node-root-2',
            access_count: 15,
            last_accessed: new Date().toISOString(),
            is_active: true,
            energy_score: 0.91,
          },
        ],
      },
    ],
  };
}

function getMockVectorsData(): VectorsResponse {
  const points = [
    { id: 'node-root-1', tree_id: 'tree-alpha', level: 2, label: 'Root Summary: trace-lite lightweight architecture...', x: 0.2, y: 4.5, z: 1.1 },
    { id: 'node-l1-1', tree_id: 'tree-alpha', level: 1, label: 'Summary L1: Spine storage, SQLite database layer...', x: -3.4, y: 2.1, z: -0.8 },
    { id: 'node-l1-2', tree_id: 'tree-alpha', level: 1, label: 'Summary L1: RAPTOR tree clustering algorithms...', x: 3.1, y: 2.3, z: 0.5 },
    { id: 'node-leaf-1', tree_id: 'tree-alpha', level: 0, label: 'Leaf Node 1: SQLite schema definition...', x: -5.2, y: -1.2, z: -2.1 },
    { id: 'node-leaf-2', tree_id: 'tree-alpha', level: 0, label: 'Leaf Node 2: Spine atomizer breaking markdown...', x: -4.1, y: -2.8, z: -1.4 },
    { id: 'node-leaf-3', tree_id: 'tree-alpha', level: 0, label: 'Leaf Node 3: Event stream log persistence...', x: -2.8, y: -3.5, z: -0.3 },
    { id: 'node-leaf-4', tree_id: 'tree-alpha', level: 0, label: 'Leaf Node 4: Gaussian mixture model clustering...', x: 2.4, y: -1.5, z: 1.8 },
    { id: 'node-leaf-5', tree_id: 'tree-alpha', level: 0, label: 'Leaf Node 5: LanceDB vector embedding store...', x: 4.8, y: -2.1, z: 2.9 },
    { id: 'node-leaf-6', tree_id: 'tree-alpha', level: 0, label: 'Leaf Node 6: Energy calculation decay half-life...', x: 3.9, y: -3.8, z: 0.2 },
    { id: 'node-root-2', tree_id: 'tree-beta', level: 1, label: 'Root Summary: LATTICE multi-stage router...', x: -0.8, y: 1.2, z: 5.4 },
    { id: 'node-beta-leaf-1', tree_id: 'tree-beta', level: 0, label: 'Leaf 1: Dense vector dot product and cosine...', x: -2.1, y: -2.0, z: 4.8 },
    { id: 'node-beta-leaf-2', tree_id: 'tree-beta', level: 0, label: 'Leaf 2: Sparse BM25 okapi keyword matching...', x: -0.2, y: -3.1, z: 6.2 },
    { id: 'node-beta-leaf-3', tree_id: 'tree-beta', level: 0, label: 'Leaf 3: Reciprocal Rank Fusion (RRF)...', x: 1.5, y: -2.4, z: 5.1 },
    { id: 'node-beta-leaf-4', tree_id: 'tree-beta', level: 0, label: 'Leaf 4: Traversal path logger and evidence...', x: 0.9, y: -4.0, z: 3.9 },
  ];
  return { points, count: points.length };
}
