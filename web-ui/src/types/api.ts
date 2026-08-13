export interface StatusResponse {
  total_atoms: number;
  total_trees: number;
  estimated_tokens: number;
  active_nodes: number;
  total_nodes: number;
  pending_atoms: number;
  pending_trees: number;
  orphaned_atoms: number;
  needs_organization: boolean;
  needs_recovery: boolean;
  indexed_atoms: number;
  valid_summaries: number;
  /** Legacy fallback summaries; non-zero means the derived index is untrusted. */
  fallback_summaries: number;
  vector_count: number;
  active_build_id: string | null;
  validation_state: string;
  validation_errors: string[];
  validation_warnings: string[];
  /** True only for a validated active build with no pending/untrusted provenance. */
  index_trusted: boolean;
  /** Credential Manager/provider state, never a plaintext key. */
  credential_state: string;
  active_provider: string | null;
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
  node_type?: string;
  summary_provenance?: string;
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
  index_health?: { state: string; active_build_id: string | null; warnings: string[] };
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
  summary_provenance?: string | null;
}

export interface VectorsResponse {
  available: boolean;
  diagnostic: { code?: string; message?: string } | null;
  points: VectorPoint[];
  count: number;
  total_count: number;
  displayed_count: number;
  projection_method: string;
  sampled: boolean;
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
  mode: 'hybrid' | 'tree' | 'flat';
  results_count: number;
  items: QueryEvidenceItem[];
  organized_before_query?: boolean;
  needs_organization?: boolean;
  /** True when the caller explicitly searched only the last verified index. */
  forced?: boolean;
  /** Force-mode exclusion or provider/index diagnostics. */
  warnings?: string[];
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
  folders?: FolderConnection[];
}

export interface IngestResponse {
  status: string;
  atom_count: number;
  artifact_id: string;
  tree_ids: string[];
  queued: boolean;
  pending_atoms: number;
  pending_trees: number;
  needs_organization: boolean;
  needs_recovery: boolean;
}

export interface VaultSyncResponse {
  status: string;
  vault_path: string;
  connection_id?: string;
  files_scanned: number;
  files_ingested: number;
  atoms_ingested: number;
  trees_updated?: number;
  pending_atoms?: number;
  pending_trees?: number;
  needs_organization?: boolean;
  needs_recovery?: boolean;
}

export interface ConsolidateResponse {
  status: string;
  trees_updated: number;
  summaries_generated: number;
  pending_atoms: number;
  pending_trees: number;
  orphaned_atoms: number;
  needs_organization: boolean;
  needs_recovery: boolean;
}

export interface DagNode {
  id: string;
  label: string;
  level: number;
  type: string;
  summary_text?: string | null;
  summary_provenance?: string;
  atom_ids?: string[];
  parent_id?: string | null;
}

export interface DagLink {
  source: string;
  target: string;
  type: string;
}

export interface DagResponse {
  tree_id: string;
  root_id: string;
  node_count: number;
  nodes: DagNode[];
  links: DagLink[];
  topology?: string;
}

export interface ProjectItem {
  name: string;
  path: string;
  is_active: boolean;
  created_at: string;
  last_accessed: string;
  description: string;
  atom_count: number;
  tree_count: number;
  estimated_tokens: number;
  exists_on_disk: boolean;
  ownership?: 'managed' | 'external' | 'unowned';
  managed?: boolean;
  pending_atoms?: number;
  pending_trees?: number;
  orphaned_atoms?: number;
  needs_organization?: boolean;
  needs_recovery?: boolean;
}

export interface FolderConnection {
  connection_id: string;
  root_path: string;
  kind: string;
  metadata: Record<string, unknown>;
  created_at: string;
  last_synced_at: string;
}

export interface ProjectsResponse {
  active_project: string | null;
  projects: ProjectItem[];
  data_dir?: string | null;
  data_dir_locked?: boolean;
  server_paths_allowed?: boolean;
}

export interface SavedProviderInfo {
  provider_id: string;
  name: string;
  model: string;
  api_base: string | null;
  api_version: string | null;
  env_var: string | null;
  /** Presence in Credential Manager; the secret itself is never serialized. */
  has_api_key: boolean;
  credential_state?: string;
}

export interface AvailableProviderInfo {
  id: string;
  name: string;
  description: string;
  default_model: string;
  popular_models: string[];
  requires_api_key: boolean;
  requires_api_base: boolean;
  default_api_base: string | null;
  requires_api_version: boolean;
  default_api_version: string | null;
}

export interface ConfigResponse {
  active_provider: string | null;
  active_model: string | null;
  api_base: string | null;
  api_version: string | null;
  auto_load_models: boolean;
  saved_providers: Record<string, SavedProviderInfo>;
  available_providers: AvailableProviderInfo[];
}

export interface ModelStatusResponse {
  auto_load_models: boolean;
  enabled: boolean;
  model: string | null;
  embedding_model: string | null;
  status: 'not_loaded' | 'loading' | 'ready' | 'failed' | string;
  loaded: boolean;
  loading: boolean;
  error: string | null;
  embedding: {
    model: string | null;
    status: 'not_loaded' | 'loading' | 'ready' | 'failed' | string;
    loaded: boolean;
    error: string | null;
  };
  llm: { status: string; contacted: boolean };
}

export interface TestConfigResponse {
  success: boolean;
  message: string;
}

export interface WorkspaceResponse {
  project: ProjectItem | Record<string, unknown> | null;
  status: StatusResponse;
  sources: {
    count: number;
    artifacts: SpineArtifact[];
  };
  folders: FolderConnection[];
  next_action: 'create_project' | 'add_source' | 'recovery' | 'organize' | 'ask';
}
