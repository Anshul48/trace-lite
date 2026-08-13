import React, { useEffect, useMemo, useState } from 'react';
import { Command, Inbox, ListTree, RefreshCw, Search, Settings, Sparkles, Users, X } from 'lucide-react';
import { TabType } from './Header';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  setActiveTab: (tab: TabType) => void;
  onOpenIngest: () => void;
  onOpenProjects: () => void;
  onOpenSettings: () => void;
  onOrganize: () => void;
  onRebuild: () => void;
  onRefresh: () => void;
}

export const CommandPalette: React.FC<Props> = ({ isOpen, onClose, setActiveTab, onOpenIngest, onOpenProjects, onOpenSettings, onOrganize, onRebuild, onRefresh }) => {
  const [search, setSearch] = useState('');
  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isOpen, onClose]);

  const actions = useMemo(() => [
    { label: 'Go to Home', group: 'Navigate', icon: <Sparkles size={16} />, run: () => setActiveTab('home') },
    { label: 'Ask a question', group: 'Navigate', icon: <Search size={16} />, run: () => setActiveTab('ask') },
    { label: 'Review sources', group: 'Navigate', icon: <Inbox size={16} />, run: () => setActiveTab('sources') },
    { label: 'Open Organize', group: 'Navigate', icon: <ListTree size={16} />, run: () => setActiveTab('organize') },
    { label: 'Explore internals', group: 'Navigate', icon: <Command size={16} />, run: () => setActiveTab('explore') },
    { label: 'Add source', group: 'Actions', icon: <Inbox size={16} />, run: onOpenIngest },
    { label: 'Organize pending content', group: 'Actions', icon: <ListTree size={16} />, run: onOrganize },
    { label: 'Rebuild structure', group: 'Actions', icon: <RefreshCw size={16} />, run: onRebuild },
    { label: 'Manage projects', group: 'Workspace', icon: <Users size={16} />, run: onOpenProjects },
    { label: 'Open settings', group: 'Workspace', icon: <Settings size={16} />, run: onOpenSettings },
    { label: 'Refresh workspace', group: 'Workspace', icon: <RefreshCw size={16} />, run: onRefresh },
  ].filter((action) => action.label.toLowerCase().includes(search.toLowerCase())), [search, onOpenIngest, onOpenProjects, onOpenSettings, onOrganize, onRebuild, onRefresh, setActiveTab]);

  if (!isOpen) return null;
  const run = (action: typeof actions[number]) => { action.run(); onClose(); setSearch(''); };
  return (
    <div className="modal-scrim" onMouseDown={onClose}>
      <div className="command-dialog" role="dialog" aria-modal="true" aria-label="Command palette" onMouseDown={(event) => event.stopPropagation()}>
        <div className="command-search"><Search size={17} /><input autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search workspace actions…" /><kbd>Esc</kbd><button onClick={onClose} aria-label="Close command palette"><X size={16} /></button></div>
        <div className="command-list">{actions.length === 0 ? <div className="empty-state">No actions found.</div> : actions.map((action) => <button className="command-item" key={action.label} onClick={() => run(action)}><span>{action.icon}{action.label}</span><small>{action.group}</small></button>)}</div>
      </div>
    </div>
  );
};
