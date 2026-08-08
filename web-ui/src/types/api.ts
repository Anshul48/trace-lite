export interface StatusResponse {
  total_atoms: number;
  total_trees: number;
  estimated_tokens: number;
  active_nodes: number;
  total_nodes: number;
  data_dir: string;
  embedding_model: string;
  llm_model: string;
}

export interface TreeNode {
  node_id: string;
  tree_id: string;
  level: number;
  summary_text: string;
  atom_ids: string[];
  child_node_ids: string[];
  parent_node_id: string | null;
  access_count: number;
  last_accessed: string | null;
  is_active: boolean;
  energy_score: number;
}

export interface TreeData {
  tree_id: string;
  name: string;
  description: string;
  root_node_id: string;
  leaf_count: number;
  depth: number;
  node_count: number;
  nodes: TreeNode[];
}

export interface ForestResponse {
  total_trees: number;
  trees: TreeData[];
}

export interface VectorPoint {
  id: string;
  tree_id: string;
  level: number;
  label: string;
  x: number;
  y: number;
  z: number;
}

export interface VectorsResponse {
  points: VectorPoint[];
  count: number;
}

export interface QueryEvidenceItem {
  atom_id: string;
  content: string;
  score: number;
  source: string;
  traversal_path: string[] | null;
}

export interface QueryResponse {
  query_text: string;
  mode: string;
  results_count: number;
  items: QueryEvidenceItem[];
}

export interface SpineArtifact {
  artifact_id: string;
  document_name: string;
  source_uri: string | null;
  created_at: string | null;
  atom_count: number;
  content_preview: string;
}

export interface SpineEvent {
  event_id: string;
  event_type: string;
  timestamp: string | null;
  payload: Record<string, any>;
}

export interface SpineResponse {
  artifacts_count: number;
  artifacts: SpineArtifact[];
  events: SpineEvent[];
}
