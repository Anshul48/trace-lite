/*
 * Copyright 2026 trace-lite contributors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import {
  App,
  ItemView,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  TFile,
  WorkspaceLeaf,
  requestUrl,
  setIcon
} from 'obsidian';

export const TRACE_LITE_VIEW_TYPE = 'trace-lite-view';

export interface TraceLiteSettings {
  apiUrl: string;
  autoSyncOnModify: boolean;
  autoSyncOnCreate: boolean;
  defaultMode: 'hybrid' | 'tree' | 'flat';
  topK: number;
}

export const DEFAULT_SETTINGS: TraceLiteSettings = {
  apiUrl: 'http://127.0.0.1:8420',
  autoSyncOnModify: true,
  autoSyncOnCreate: true,
  defaultMode: 'hybrid',
  topK: 5
};

export interface QueryItem {
  atom_id?: string;
  score: number;
  traversal_path?: string[];
  source_artifact?: {
    artifact_id?: string;
    document_name?: string;
  };
  atom: {
    atom_id: string;
    content: string;
    token_count?: number;
    chronological_order?: string;
  };
}

export interface QueryResponse {
  query_text: string;
  mode: string;
  total_results: number;
  items: QueryItem[];
}

export interface StatusResponse {
  status?: string;
  total_atoms?: number;
  total_trees?: number;
  active_nodes?: number;
  total_nodes?: number;
  estimated_tokens?: number;
  [key: string]: any;
}

export class TraceLiteSidebarView extends ItemView {
  plugin: TraceLitePlugin;
  statusDotEl!: HTMLElement;
  statusTextEl!: HTMLElement;
  searchInputEl!: HTMLInputElement;
  modeSelectEl!: HTMLSelectElement;
  topKInputEl!: HTMLInputElement;
  resultsContainerEl!: HTMLElement;
  isSearching = false;

  constructor(leaf: WorkspaceLeaf, plugin: TraceLitePlugin) {
    super(leaf);
    this.plugin = plugin;
  }

  getViewType(): string {
    return TRACE_LITE_VIEW_TYPE;
  }

  getDisplayText(): string {
    return 'trace-lite RAG Assistant';
  }

  getIcon(): string {
    return 'brain';
  }

  async onOpen(): Promise<void> {
    const container = this.contentEl;
    container.empty();
    container.addClass('trace-lite-sidebar');

    // 1. Header & Connection Status
    const headerEl = container.createDiv({ cls: 'trace-lite-header' });
    
    const titleEl = headerEl.createDiv({ cls: 'trace-lite-header-title' });
    const iconEl = titleEl.createSpan({ cls: 'trace-lite-icon' });
    setIcon(iconEl, 'brain');
    titleEl.createSpan({ text: 'trace-lite Assistant' });

    const statusBadgeEl = headerEl.createDiv({ cls: 'trace-lite-status-badge' });
    this.statusDotEl = statusBadgeEl.createDiv({ cls: 'trace-lite-status-dot' });
    this.statusTextEl = statusBadgeEl.createSpan({ text: 'Checking...' });

    // 2. Actions Bar (Sync button & Quick refresh)
    const actionsBarEl = container.createDiv({ cls: 'trace-lite-actions-bar' });
    const syncBtn = actionsBarEl.createEl('button', {
      cls: 'trace-lite-btn trace-lite-btn-secondary',
      text: 'Sync Vault'
    });
    const syncIcon = syncBtn.createSpan({ cls: 'trace-lite-btn-icon' });
    setIcon(syncIcon, 'refresh-cw');
    syncBtn.prepend(syncIcon);
    syncBtn.addEventListener('click', async () => {
      await this.plugin.syncEntireVault();
    });

    const statusRefreshBtn = actionsBarEl.createEl('button', {
      cls: 'trace-lite-btn trace-lite-btn-secondary',
      text: 'Status'
    });
    statusRefreshBtn.addEventListener('click', async () => {
      await this.checkStatus();
    });

    // 3. Search Inputs & Controls
    const searchContainerEl = container.createDiv({ cls: 'trace-lite-search-container' });
    
    const inputWrapperEl = searchContainerEl.createDiv({ cls: 'trace-lite-search-input-wrapper' });
    this.searchInputEl = inputWrapperEl.createEl('input', {
      cls: 'trace-lite-search-input',
      type: 'text',
      placeholder: 'Ask or search knowledge base...'
    });

    const searchBtn = inputWrapperEl.createEl('button', {
      cls: 'trace-lite-btn',
      text: 'Search'
    });

    this.searchInputEl.addEventListener('keydown', (e: KeyboardEvent) => {
      if (e.key === 'Enter') {
        this.runSearch();
      }
    });

    searchBtn.addEventListener('click', () => {
      this.runSearch();
    });

    // Controls Row (Mode & TopK)
    const controlsRowEl = searchContainerEl.createDiv({ cls: 'trace-lite-controls-row' });
    
    const modeGroupEl = controlsRowEl.createDiv({ cls: 'trace-lite-control-group' });
    modeGroupEl.createSpan({ text: 'Mode:' });
    this.modeSelectEl = modeGroupEl.createEl('select', { cls: 'trace-lite-select' });
    
    ['hybrid', 'tree', 'flat'].forEach((m) => {
      const opt = this.modeSelectEl.createEl('option', { value: m, text: m.toUpperCase() });
      if (m === this.plugin.settings.defaultMode) {
        opt.selected = true;
      }
    });

    const topKGroupEl = controlsRowEl.createDiv({ cls: 'trace-lite-control-group' });
    topKGroupEl.createSpan({ text: 'Top K:' });
    this.topKInputEl = topKGroupEl.createEl('input', {
      cls: 'trace-lite-select',
      type: 'number',
      value: String(this.plugin.settings.topK)
    });
    this.topKInputEl.style.width = '45px';
    this.topKInputEl.min = '1';
    this.topKInputEl.max = '20';

    // 4. Results Header & List
    container.createDiv({ cls: 'trace-lite-results-header', text: 'Hierarchical Evidence & Results' });
    this.resultsContainerEl = container.createDiv({ cls: 'trace-lite-results-list' });
    this.renderEmptyState('Enter a query above to search your hierarchical knowledge graph.');

    // Initial daemon connection check
    await this.checkStatus();
  }

  async checkStatus(): Promise<boolean> {
    try {
      this.statusDotEl.className = 'trace-lite-status-dot syncing';
      this.statusTextEl.setText('Connecting...');

      const response = await requestUrl({
        url: `${this.plugin.settings.apiUrl}/api/status`,
        method: 'GET'
      });

      if (response.status === 200) {
        const data: StatusResponse = response.json;
        this.statusDotEl.className = 'trace-lite-status-dot connected';
        const atoms = data.total_atoms ?? 0;
        const trees = data.total_trees ?? 0;
        this.statusTextEl.setText(`Connected (${atoms} atoms, ${trees} trees)`);
        return true;
      } else {
        throw new Error(`HTTP ${response.status}`);
      }
    } catch (err) {
      this.statusDotEl.className = 'trace-lite-status-dot disconnected';
      this.statusTextEl.setText('Offline');
      return false;
    }
  }

  async runSearch(): Promise<void> {
    const queryText = this.searchInputEl.value.trim();
    if (!queryText) {
      new Notice('Please enter a search query.');
      return;
    }

    if (this.isSearching) return;
    this.isSearching = true;

    this.resultsContainerEl.empty();
    const loadingState = this.resultsContainerEl.createDiv({ cls: 'trace-lite-empty-state' });
    const spinner = loadingState.createDiv({ cls: 'trace-lite-spinner' });
    loadingState.createDiv({ text: 'Searching RAPTOR summary trees & LATTICE vectors...' });

    const mode = this.modeSelectEl.value;
    const topK = parseInt(this.topKInputEl.value) || 5;

    try {
      const response = await requestUrl({
        url: `${this.plugin.settings.apiUrl}/api/query`,
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query_text: queryText,
          top_k: topK,
          mode: mode
        })
      });

      if (response.status === 200) {
        const data: QueryResponse = response.json;
        this.renderResults(data);
      } else {
        new Notice(`trace-lite query error: HTTP ${response.status}`);
        this.renderEmptyState(`Error querying server (HTTP ${response.status})`);
      }
    } catch (err) {
      console.error('trace-lite search failed:', err);
      new Notice(`Failed to connect to trace-lite server at ${this.plugin.settings.apiUrl}`);
      this.renderEmptyState('Failed to connect to trace-lite daemon server.');
      this.checkStatus();
    } finally {
      this.isSearching = false;
    }
  }

  renderResults(data: QueryResponse): void {
    this.resultsContainerEl.empty();

    if (!data.items || data.items.length === 0) {
      this.renderEmptyState(`No matching evidence found for "${data.query_text}".`);
      return;
    }

    data.items.forEach((item, index) => {
      const card = this.resultsContainerEl.createDiv({ cls: 'trace-lite-evidence-card' });

      // Top row: Source Document Title & Score Badge
      const topRow = card.createDiv({ cls: 'trace-lite-card-top' });
      
      const docInfo = topRow.createDiv({ cls: 'trace-lite-doc-info' });
      const fileIcon = docInfo.createSpan();
      setIcon(fileIcon, 'file-text');
      
      const docName = item.source_artifact?.document_name || 'Untitled Document';
      docInfo.createSpan({ cls: 'trace-lite-doc-name', text: docName });

      // Score Badge with color scaling
      const scorePct = Math.round(item.score * 100);
      let scoreClass = 'low-score';
      if (item.score >= 0.7) scoreClass = 'high-score';
      else if (item.score >= 0.4) scoreClass = 'medium-score';

      const scoreBadge = topRow.createDiv({
        cls: `trace-lite-score-badge ${scoreClass}`,
        text: `${scorePct}% match`
      });

      // Traversal Path Highlights (RAPTOR / LATTICE navigation)
      if (item.traversal_path && item.traversal_path.length > 0) {
        const pathEl = card.createDiv({ cls: 'trace-lite-path' });
        pathEl.createSpan({ text: 'Hierarchy Path:' });
        
        item.traversal_path.forEach((step, idx) => {
          if (idx > 0) {
            pathEl.createSpan({ cls: 'trace-lite-path-separator', text: '➔' });
          }
          pathEl.createSpan({ cls: 'trace-lite-path-node', text: step });
        });
      }

      // Content Snippet
      card.createDiv({
        cls: 'trace-lite-snippet',
        text: item.atom.content
      });

      // Card Footer with click-to-open action hint
      const footerEl = card.createDiv({ cls: 'trace-lite-card-footer' });
      const linkIcon = footerEl.createSpan();
      setIcon(linkIcon, 'external-link');
      footerEl.createSpan({ text: 'Open in Vault' });

      // Click event listener to open note in Obsidian workspace
      card.addEventListener('click', () => {
        this.openNoteInVault(docName);
      });
    });
  }

  renderEmptyState(message: string): void {
    this.resultsContainerEl.empty();
    const emptyEl = this.resultsContainerEl.createDiv({ cls: 'trace-lite-empty-state' });
    const iconSpan = emptyEl.createDiv({ cls: 'trace-lite-empty-icon' });
    setIcon(iconSpan, 'search');
    emptyEl.createDiv({ text: message });
  }

  async openNoteInVault(docName: string): Promise<void> {
    // 1. Try exact path match first
    let file = this.app.vault.getAbstractFileByPath(docName);
    
    // 2. Try adding .md extension if omitted
    if (!file && !docName.endsWith('.md')) {
      file = this.app.vault.getAbstractFileByPath(`${docName}.md`);
    }

    // 3. Search by file name / linkpath destination in metadata cache
    if (!file || !(file instanceof TFile)) {
      const firstDest = this.app.metadataCache.getFirstLinkpathDest(docName, '');
      if (firstDest) {
        file = firstDest;
      }
    }

    // 4. Fallback search among all markdown files in vault
    if (!file || !(file instanceof TFile)) {
      const allFiles = this.app.vault.getMarkdownFiles();
      const matched = allFiles.find((f) => f.path.includes(docName) || f.name.includes(docName));
      if (matched) {
        file = matched;
      }
    }

    if (file && file instanceof TFile) {
      const leaf = this.app.workspace.getLeaf(false);
      await leaf.openFile(file);
      new Notice(`Opened note: ${file.basename}`);
    } else {
      // Fallback: try openLinkText
      this.app.workspace.openLinkText(docName, '', true);
    }
  }
}

export class TraceLiteSettingTab extends PluginSettingTab {
  plugin: TraceLitePlugin;

  constructor(app: App, plugin: TraceLitePlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    containerEl.createEl('h2', { text: 'trace-lite AI & RAG Assistant Settings' });

    new Setting(containerEl)
      .setName('trace-lite API Base URL')
      .setDesc('Hostname and port for local trace-lite Python daemon (default http://127.0.0.1:8420).')
      .addText((text) =>
        text
          .setPlaceholder('http://127.0.0.1:8420')
          .setValue(this.plugin.settings.apiUrl)
          .onChange(async (value) => {
            this.plugin.settings.apiUrl = value.trim().replace(/\/+$/, '');
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('Auto-sync on Note Modification')
      .setDesc('Automatically re-ingest notes into trace-lite when modified in Obsidian.')
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.autoSyncOnModify)
          .onChange(async (value) => {
            this.plugin.settings.autoSyncOnModify = value;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('Auto-sync on Note Creation')
      .setDesc('Automatically ingest new markdown notes into trace-lite upon creation.')
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.autoSyncOnCreate)
          .onChange(async (value) => {
            this.plugin.settings.autoSyncOnCreate = value;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('Default Search Mode')
      .setDesc('Default search algorithm mode for RAG queries (hybrid uses LATTICE + tree summaries).')
      .addDropdown((dropdown) =>
        dropdown
          .addOption('hybrid', 'Hybrid (Vector + RAPTOR Trees)')
          .addOption('tree', 'Tree (RAPTOR Summary Forest)')
          .addOption('flat', 'Flat (Vector Similarity)')
          .setValue(this.plugin.settings.defaultMode)
          .onChange(async (value: 'hybrid' | 'tree' | 'flat') => {
            this.plugin.settings.defaultMode = value;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName('Default Top K Results')
      .setDesc('Number of evidence cards to retrieve per query (1 to 20).')
      .addSlider((slider) =>
        slider
          .setLimits(1, 20, 1)
          .setValue(this.plugin.settings.topK)
          .setDynamicTooltip()
          .onChange(async (value) => {
            this.plugin.settings.topK = value;
            await this.plugin.saveSettings();
          })
      );

    // Test Connection Button
    new Setting(containerEl)
      .setName('Test Daemon Connection')
      .setDesc('Verify connection status with your trace-lite REST API server.')
      .addButton((button) =>
        button
          .setButtonText('Test Connection')
          .setCta()
          .onClick(async () => {
            try {
              const res = await requestUrl({
                url: `${this.plugin.settings.apiUrl}/api/status`,
                method: 'GET'
              });

              if (res.status === 200) {
                const info = res.json;
                new Notice(`✓ Successfully connected to trace-lite! (${info.total_atoms ?? 0} atoms indexed)`);
              } else {
                new Notice(`❌ Failed to connect: Server returned status code ${res.status}`);
              }
            } catch (e) {
              new Notice(`❌ Connection failed. Check if trace-lite daemon is running at ${this.plugin.settings.apiUrl}`);
            }
          })
      );
  }
}

export default class TraceLitePlugin extends Plugin {
  settings!: TraceLiteSettings;

  async onload(): Promise<void> {
    await this.loadSettings();

    // Register Sidebar View
    this.registerView(
      TRACE_LITE_VIEW_TYPE,
      (leaf) => new TraceLiteSidebarView(leaf, this)
    );

    // Register Command Palette Commands
    this.addCommand({
      id: 'open-trace-lite-assistant',
      name: 'trace-lite: Open RAG Assistant',
      callback: () => {
        this.activateView();
      }
    });

    this.addCommand({
      id: 'sync-current-vault',
      name: 'trace-lite: Sync Current Vault',
      callback: async () => {
        await this.syncEntireVault();
      }
    });

    // Register Settings Tab
    this.addSettingTab(new TraceLiteSettingTab(this.app, this));

    // Register Vault Event Listeners
    this.registerEvent(
      this.app.vault.on('modify', async (file) => {
        if (this.settings.autoSyncOnModify && file instanceof TFile && file.extension === 'md') {
          await this.syncNoteToTraceLite(file, 'modified');
        }
      })
    );

    this.registerEvent(
      this.app.vault.on('create', async (file) => {
        if (this.settings.autoSyncOnCreate && file instanceof TFile && file.extension === 'md') {
          await this.syncNoteToTraceLite(file, 'created');
        }
      })
    );
  }

  async activateView(): Promise<void> {
    const { workspace } = this.app;

    let leaf: WorkspaceLeaf | null = null;
    const leaves = workspace.getLeavesOfType(TRACE_LITE_VIEW_TYPE);

    if (leaves.length > 0) {
      leaf = leaves[0];
    } else {
      leaf = workspace.getRightLeaf(false);
      if (leaf) {
        await leaf.setViewState({ type: TRACE_LITE_VIEW_TYPE, active: true });
      }
    }

    if (leaf) {
      workspace.revealLeaf(leaf);
    }
  }

  async syncNoteToTraceLite(file: TFile, eventType: string): Promise<void> {
    try {
      const content = await this.app.vault.read(file);
      const res = await requestUrl({
        url: `${this.settings.apiUrl}/api/ingest`,
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: content,
          document_name: file.path
        })
      });

      if (res.status === 200) {
        console.log(`[trace-lite] Auto-synced ${file.path} (${eventType})`);
      }
    } catch (err) {
      console.warn(`[trace-lite] Auto-sync failed for ${file.path}:`, err);
    }
  }

  async syncEntireVault(): Promise<void> {
    const mdFiles = this.app.vault.getMarkdownFiles();
    if (mdFiles.length === 0) {
      new Notice('No markdown files found in vault to sync.');
      return;
    }

    new Notice(`Starting trace-lite sync for ${mdFiles.length} note(s)...`);
    let synced = 0;
    let failed = 0;

    for (const file of mdFiles) {
      try {
        const content = await this.app.vault.read(file);
        const res = await requestUrl({
          url: `${this.settings.apiUrl}/api/ingest`,
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: content,
            document_name: file.path
          })
        });

        if (res.status === 200) {
          synced++;
        } else {
          failed++;
        }
      } catch (err) {
        failed++;
      }
    }

    if (failed === 0) {
      new Notice(`✓ trace-lite: Successfully synced all ${synced} vault note(s)!`);
    } else {
      new Notice(`trace-lite sync finished: ${synced} succeeded, ${failed} failed.`);
    }

    // Refresh view status if open
    const leaves = this.app.workspace.getLeavesOfType(TRACE_LITE_VIEW_TYPE);
    for (const leaf of leaves) {
      if (leaf.view instanceof TraceLiteSidebarView) {
        await leaf.view.checkStatus();
      }
    }
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }
}
