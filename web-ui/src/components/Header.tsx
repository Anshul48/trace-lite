import React, { useEffect, useRef, useState } from 'react';
import {
  BookOpen,
  ChevronDown,
  Command,
  Compass,
  FolderOpen,
  Inbox,
  LayoutDashboard,
  Library,
  ListTree,
  Plus,
  RefreshCw,
  Settings,
  Sparkles,
  WandSparkles,
} from 'lucide-react';
import { ProjectsResponse, StatusResponse } from '../types/api';
import { switchProject } from '../services/api';

export type TabType = 'home' | 'ask' | 'sources' | 'organize' | 'explore';

interface HeaderProps {
  activeTab: TabType;
  setActiveTab: (tab: TabType) => void;
  onRefresh: () => void;
  onOpenIngestModal: () => void;
  onOpenProjectsModal: () => void;
  onOpenSettingsModal: () => void;
  onOpenCommandPalette: () => void;
  onOrganize: () => void;
  onRebuild: () => void;
  isLoading: boolean;
  isOrganizing: boolean;
  isRebuilding: boolean;
  status: StatusResponse | null;
  projectsData: ProjectsResponse | null;
}

const navigation: Array<{ id: TabType; label: string; icon: React.ElementType }> = [
  { id: 'home', label: 'Home', icon: LayoutDashboard },
  { id: 'ask', label: 'Ask', icon: Sparkles },
  { id: 'sources', label: 'Sources', icon: Inbox },
  { id: 'organize', label: 'Organize', icon: WandSparkles },
  { id: 'explore', label: 'Explore', icon: Compass },
];

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  setActiveTab,
  onRefresh,
  onOpenIngestModal,
  onOpenProjectsModal,
  onOpenSettingsModal,
  onOpenCommandPalette,
  onOrganize,
  onRebuild,
  isLoading,
  isOrganizing,
  isRebuilding,
  status,
  projectsData,
}) => {
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const projectMenuRef = useRef<HTMLDivElement>(null);
  const activeProject = projectsData?.active_project;
  const canOrganize = Boolean(status?.needs_organization);
  const projectLocked = Boolean(projectsData?.data_dir_locked);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (projectMenuRef.current && !projectMenuRef.current.contains(event.target as Node)) {
        setProjectMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  const handleSwitch = async (name: string) => {
    setProjectMenuOpen(false);
    if (name === activeProject) return;
    await switchProject(name);
    onRefresh();
  };

  return (
    <header className="workspace-header">
      <div className="brand-block" role="banner">
        <div className="brand-mark" aria-hidden="true"><Library size={19} /></div>
        <div>
          <div className="brand-name">trace-lite <span className="brand-tag">workspace</span></div>
          <div className="brand-subtitle">A quiet place for source-grounded answers</div>
        </div>
      </div>

      <nav className="workspace-nav" aria-label="Workspace sections">
        {navigation.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={`nav-item ${activeTab === id ? 'is-active' : ''}`}
            onClick={() => setActiveTab(id)}
            aria-current={activeTab === id ? 'page' : undefined}
          >
            <Icon size={15} />
            <span>{label}</span>
          </button>
        ))}
      </nav>

      <div className="header-actions">
        <div className="project-switcher" ref={projectMenuRef}>
          <button
            className="quiet-button project-button"
            onClick={() => setProjectMenuOpen((open) => !open)}
            aria-expanded={projectMenuOpen}
            aria-haspopup="menu"
            title={projectLocked ? 'Workspace is locked to its explicit data directory' : 'Switch project'}
          >
            <FolderOpen size={15} />
            <span>{activeProject || 'No project selected'}</span>
            <ChevronDown size={14} />
          </button>
          {projectMenuOpen && (
            <div className="project-menu" role="menu">
              {projectsData?.projects.map((project) => (
                <button
                  role="menuitem"
                  key={project.name}
                  className={`project-menu-item ${project.name === activeProject ? 'is-active' : ''}`}
                  onClick={() => { if (!projectLocked) void handleSwitch(project.name); }}
                  disabled={projectLocked}
                >
                  <span>{project.name}</span>
                  <small>{project.atom_count.toLocaleString()} atoms</small>
                </button>
              ))}
              <button className="project-menu-footer" onClick={onOpenProjectsModal}>
                <Plus size={14} /> Manage projects
              </button>
            </div>
          )}
        </div>
        <button className="icon-button" onClick={onOpenCommandPalette} title="Open command palette (Ctrl+K)" aria-label="Open command palette">
          <Command size={16} />
        </button>
        <button className="icon-button" onClick={onRefresh} disabled={isLoading} title="Refresh workspace" aria-label="Refresh workspace">
          <RefreshCw size={16} className={isLoading ? 'spin' : ''} />
        </button>
        <button className="primary-button compact" onClick={onOpenIngestModal}>
          <Plus size={16} /> Add source
        </button>
        <button className="icon-button" onClick={onOpenSettingsModal} title="Settings" aria-label="Settings">
          <Settings size={16} />
        </button>
      </div>

      {activeTab === 'organize' && (
        <div className="header-secondary-actions">
          <button className="secondary-button" onClick={onRebuild} disabled={isRebuilding || isOrganizing}>
            <ListTree size={15} /> {isRebuilding ? 'Rebuilding…' : 'Rebuild structure'}
          </button>
        </div>
      )}
      {canOrganize && activeTab !== 'organize' && (
        <button className="organize-button" onClick={onOrganize} disabled={isOrganizing || isRebuilding}>
          <WandSparkles size={15} /> {isOrganizing ? 'Organizing…' : 'Organize now'}
        </button>
      )}
      <span className="header-bookmark" aria-hidden="true"><BookOpen size={14} /></span>
    </header>
  );
};
