import {
  ConfigResponse,
  ConsolidateResponse,
  DagResponse,
  ForestResponse,
  IngestResponse,
  ModelStatusResponse,
  ProjectsResponse,
  QueryResponse,
  SpineResponse,
  StatusResponse,
  VaultSyncResponse,
  VectorsResponse,
  WorkspaceResponse,
} from '../types/api';

const API_BASE = '/api';

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const code = body.error_code ? ` [${body.error_code}${body.stage ? `/${body.stage}` : ''}]` : '';
    throw new Error(`${body.detail || `Request failed (${response.status})`}${code}`);
  }
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const fetchStatus = (): Promise<StatusResponse> => requestJson('/status');
export const fetchTrees = (): Promise<ForestResponse> => requestJson('/trees');
export const fetchVectors = (): Promise<VectorsResponse> => requestJson('/vectors');
export const fetchSpine = (): Promise<SpineResponse> => requestJson('/spine');
export const fetchWorkspace = (): Promise<WorkspaceResponse> => requestJson('/workspace');
export const fetchProjects = (): Promise<ProjectsResponse> => requestJson('/projects');
export const fetchConfig = (): Promise<ConfigResponse> => requestJson('/config');
export const fetchModelStatus = (): Promise<ModelStatusResponse> => requestJson('/models/status');

export async function executeQuery(
  queryText: string,
  topK = 5,
  mode: 'hybrid' | 'tree' | 'flat' = 'hybrid',
  force = false,
): Promise<QueryResponse> {
  // Force is opt-in: the server searches only the last verified flat index,
  // excludes pending captures, and makes no LLM call.
  return requestJson('/query', json({ query_text: queryText, top_k: topK, mode, force }));
}

export function ingestText(text: string, documentName?: string): Promise<IngestResponse> {
  return requestJson('/sources/text', json({ text, document_name: documentName }));
}

export function uploadSource(file: File, documentName?: string): Promise<IngestResponse> {
  return file.text().then((content) =>
    requestJson('/sources/upload', json({
      content,
      file_name: file.name,
      document_name: documentName || file.name,
    })),
  );
}

export function ingestFile(filePath: string, documentName?: string): Promise<IngestResponse> {
  return requestJson('/ingest', json({ file_path: filePath, document_name: documentName }));
}

export function syncVault(
  vaultPath: string,
  organizeAfterSync = false,
): Promise<VaultSyncResponse> {
  return requestJson('/sources/folders/sync', json({
    folder_path: vaultPath,
    organize_after_sync: organizeAfterSync,
  }));
}

export function organizeSources(): Promise<ConsolidateResponse> {
  return requestJson('/organize', json({}));
}

export function consolidateTrees(treeId?: string): Promise<ConsolidateResponse> {
  return requestJson('/consolidate', json({ tree_id: treeId }));
}

export function fetchTreeDag(treeId: string): Promise<DagResponse> {
  return requestJson(`/v1/trees/${encodeURIComponent(treeId)}/dag`);
}

export function switchProject(
  name: string,
): Promise<{ status: string; active_project: string | null; path: string }> {
  return requestJson('/projects/switch', json({ name }));
}

export function createProject(
  name: string,
  path?: string,
  description?: string,
): Promise<{ status: string; project: unknown }> {
  return requestJson('/projects/create', json({ name, path, description }));
}

export function deleteProject(
  name: string,
  purgeExternal = false,
): Promise<{ status: string; active_project: string | null }> {
  const suffix = purgeExternal ? '?purge_external=true' : '';
  return requestJson(`/projects/${encodeURIComponent(name)}${suffix}`, { method: 'DELETE' });
}

export function saveConfig(
  providerId: string,
  apiKey?: string,
  model?: string,
  apiBase?: string,
  apiVersion?: string,
): Promise<{ status: string; config: Record<string, unknown> }> {
  return requestJson('/config', json({
    provider_id: providerId,
    api_key: apiKey,
    model,
    api_base: apiBase,
    api_version: apiVersion,
  }));
}

export function setAutoLoadModels(enabled: boolean): Promise<{
  status: string;
  enabled: boolean;
  auto_load_models: boolean;
  models: ModelStatusResponse;
}> {
  return requestJson('/config/auto-load', json({ enabled }));
}

export function getExportUrl(): string {
  return `${API_BASE}/export`;
}
