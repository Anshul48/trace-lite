import React, { useState } from 'react';
import {
  X,
  FolderPlus,
  CheckCircle2,
  Trash2,
  Database,
  Layers,
  HardDrive,
  FileText,
  AlertTriangle,
} from 'lucide-react';
import { ProjectsResponse, ProjectItem } from '../types/api';
import { switchProject, createProject, deleteProject } from '../services/api';

interface ProjectManagerModalProps {
  isOpen: boolean;
  onClose: () => void;
  projectsData: ProjectsResponse | null;
  onProjectsUpdated: () => void;
}

export const ProjectManagerModal: React.FC<ProjectManagerModalProps> = ({
  isOpen,
  onClose,
  projectsData,
  onProjectsUpdated,
}) => {
  const [activeTab, setActiveTab] = useState<'list' | 'create'>('list');
  const [newProjName, setNewProjName] = useState<string>('');
  const [newProjPath, setNewProjPath] = useState<string>('');
  const [newProjDesc, setNewProjDesc] = useState<string>('');
  const [deleteConfirmName, setDeleteConfirmName] = useState<string>('');
  const [purgeExternal, setPurgeExternal] = useState<boolean>(false);
  const [projectToDelete, setProjectToDelete] = useState<ProjectItem | null>(null);
  
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const visibleTab = projectsData?.projects.length === 0 ? 'create' : activeTab;

  if (!isOpen) return null;

  const handleSwitch = async (name: string) => {
    setIsSubmitting(true);
    setErrorMsg(null);
    try {
      await switchProject(name);
      onProjectsUpdated();
      onClose();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to switch project');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newProjName.trim()) {
      setErrorMsg('Project name cannot be empty.');
      return;
    }
    setIsSubmitting(true);
    setErrorMsg(null);
    try {
      await createProject(newProjName.trim(), newProjPath.trim() || undefined, newProjDesc.trim());
      setNewProjName('');
      setNewProjPath('');
      setNewProjDesc('');
      onProjectsUpdated();
      setActiveTab('list');
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to create project');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDelete = async () => {
    if (!projectToDelete) return;
    if (deleteConfirmName !== projectToDelete.name) {
      setErrorMsg(`Typed name '${deleteConfirmName}' does not match '${projectToDelete.name}'`);
      return;
    }
    setIsSubmitting(true);
    setErrorMsg(null);
    try {
      await deleteProject(projectToDelete.name, purgeExternal);
      setProjectToDelete(null);
      setDeleteConfirmName('');
      setPurgeExternal(false);
      onProjectsUpdated();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to delete project');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.75)',
        backdropFilter: 'blur(4px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        padding: '20px',
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: '680px',
          backgroundColor: 'var(--bg-secondary)',
          border: '1px solid var(--border-color)',
          borderRadius: 'var(--radius-lg, 12px)',
          boxShadow: '0 20px 40px rgba(0,0,0,0.5)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          maxHeight: '85vh',
        }}
      >
        {/* Modal Header */}
        <div
          style={{
            padding: '16px 24px',
            borderBottom: '1px solid var(--border-color)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            backgroundColor: 'var(--bg-tertiary)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Database size={20} color="var(--accent-indigo)" />
            <h2 style={{ fontSize: '16px', fontWeight: 600, margin: 0, color: '#fff' }}>
              Database Project Manager
            </h2>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--text-muted)',
              cursor: 'pointer',
              padding: '4px',
              borderRadius: '4px',
            }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Navigation Sub-Tabs */}
        <div
          style={{
            display: 'flex',
            gap: '8px',
            padding: '12px 24px 0 24px',
            borderBottom: '1px solid var(--border-color)',
            backgroundColor: 'var(--bg-secondary)',
          }}
        >
          <button
            onClick={() => { setActiveTab('list'); setErrorMsg(null); setProjectToDelete(null); }}
            style={{
              padding: '8px 16px',
              fontSize: '13px',
              fontWeight: 600,
              backgroundColor: 'transparent',
            color: visibleTab === 'list' ? 'var(--accent-indigo)' : 'var(--text-muted)',
            borderBottom: visibleTab === 'list' ? '2px solid var(--accent-indigo)' : '2px solid transparent',
              borderTop: 'none',
              borderLeft: 'none',
              borderRight: 'none',
              cursor: 'pointer',
            }}
          >
            Projects Registry
          </button>
          <button
            onClick={() => { setActiveTab('create'); setErrorMsg(null); setProjectToDelete(null); }}
            style={{
              padding: '8px 16px',
              fontSize: '13px',
              fontWeight: 600,
              backgroundColor: 'transparent',
              color: visibleTab === 'create' ? 'var(--accent-indigo)' : 'var(--text-muted)',
              borderBottom: visibleTab === 'create' ? '2px solid var(--accent-indigo)' : '2px solid transparent',
              borderTop: 'none',
              borderLeft: 'none',
              borderRight: 'none',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <FolderPlus size={14} />
            + Create / Register Project
          </button>
        </div>

        {/* Alert Banner */}
        {errorMsg && (
          <div
            style={{
              margin: '16px 24px 0 24px',
              padding: '10px 14px',
              backgroundColor: 'rgba(239, 68, 68, 0.15)',
              border: '1px solid rgba(239, 68, 68, 0.3)',
              borderRadius: '6px',
              color: '#f87171',
              fontSize: '13px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <span>{errorMsg}</span>
            <button onClick={() => setErrorMsg(null)} style={{ background: 'none', border: 'none', color: '#f87171', cursor: 'pointer' }}>×</button>
          </div>
        )}

        {/* Content Body */}
        <div style={{ padding: '20px 24px', overflowY: 'auto', flex: 1 }}>
          {visibleTab === 'list' && (
            <div>
              {/* Project Deletion Danger Modal Confirmation */}
              {projectToDelete ? (
                <div
                  style={{
                    padding: '16px',
                    backgroundColor: 'rgba(239, 68, 68, 0.1)',
                    border: '1px solid rgba(239, 68, 68, 0.4)',
                    borderRadius: '8px',
                    marginBottom: '16px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#f87171', fontWeight: 600, fontSize: '14px', marginBottom: '8px' }}>
                    <AlertTriangle size={18} />
                    Confirm Permanent Project Deletion
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                    Project: <strong style={{ color: '#fff' }}>{projectToDelete.name}</strong> ({projectToDelete.atom_count} atoms, {projectToDelete.tree_count} trees)
                    <br />
                    Path: <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--accent-amber)' }}>{projectToDelete.path}</span>
                  </div>
                  {projectToDelete.ownership === 'external' && (
                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                      <input type="checkbox" checked={purgeExternal} onChange={(e) => setPurgeExternal(e.target.checked)} />
                      Permanently remove this external folder too
                    </label>
                  )}
                  <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>
                    Type project name <strong>{projectToDelete.name}</strong> to confirm:
                  </label>
                  <input
                    type="text"
                    value={deleteConfirmName}
                    onChange={(e) => setDeleteConfirmName(e.target.value)}
                    placeholder={projectToDelete.name}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      backgroundColor: 'var(--bg-tertiary)',
                      border: '1px solid var(--border-color)',
                      borderRadius: '6px',
                      color: '#fff',
                      fontSize: '13px',
                      marginBottom: '12px',
                      boxSizing: 'border-box',
                    }}
                  />
                  <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                    <button
                      onClick={() => setProjectToDelete(null)}
                      style={{
                        padding: '6px 12px',
                        borderRadius: '6px',
                        backgroundColor: 'var(--bg-tertiary)',
                        color: 'var(--text-secondary)',
                        border: '1px solid var(--border-color)',
                        fontSize: '12px',
                        cursor: 'pointer',
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleDelete}
                      disabled={isSubmitting || deleteConfirmName !== projectToDelete.name}
                      style={{
                        padding: '6px 14px',
                        borderRadius: '6px',
                        backgroundColor: '#ef4444',
                        color: '#fff',
                        border: 'none',
                        fontSize: '12px',
                        fontWeight: 600,
                        cursor: isSubmitting || deleteConfirmName !== projectToDelete.name ? 'not-allowed' : 'pointer',
                        opacity: deleteConfirmName === projectToDelete.name ? 1 : 0.5,
                      }}
                    >
                      {isSubmitting ? 'Deleting...' : 'Delete Database Project'}
                    </button>
                  </div>
                </div>
              ) : null}

              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                {projectsData?.projects.map((proj) => {
                  const isActive = proj.name === projectsData.active_project;
                  return (
                    <div
                      key={proj.name}
                      style={{
                        padding: '14px 16px',
                        borderRadius: '8px',
                        backgroundColor: isActive ? 'rgba(99, 102, 241, 0.08)' : 'var(--bg-tertiary)',
                        border: isActive ? '1px solid var(--accent-indigo)' : '1px solid var(--border-color)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                      }}
                    >
                      <div style={{ flex: 1, paddingRight: '16px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                          <span style={{ fontWeight: 600, fontSize: '14px', color: '#fff' }}>{proj.name}</span>
                          {isActive && (
                            <span
                              style={{
                                fontSize: '10px',
                                fontWeight: 600,
                                padding: '2px 6px',
                                borderRadius: '4px',
                                backgroundColor: 'rgba(34, 197, 94, 0.2)',
                                color: 'var(--accent-green)',
                                border: '1px solid rgba(34, 197, 94, 0.4)',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '4px',
                              }}
                            >
                              <CheckCircle2 size={11} /> ACTIVE
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>
                          {proj.description || 'No description provided.'}
                        </div>
                        <div style={{ display: 'flex', gap: '16px', fontSize: '11px', color: 'var(--text-secondary)' }}>
                          <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                            <Layers size={12} color="var(--accent-cyan)" /> {proj.atom_count} atoms
                          </span>
                          <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                            <HardDrive size={12} color="var(--accent-purple)" /> {proj.tree_count} trees
                          </span>
                          <span style={{ display: 'flex', alignItems: 'center', gap: '4px', fontFamily: 'var(--font-mono)' }}>
                            <FileText size={12} color="var(--text-muted)" /> {proj.path}
                          </span>
                        </div>
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        {!isActive ? (
                          <button
                            onClick={() => handleSwitch(proj.name)}
                            disabled={isSubmitting}
                            style={{
                              padding: '6px 14px',
                              borderRadius: '6px',
                              backgroundColor: 'var(--accent-indigo)',
                              color: '#fff',
                              fontSize: '12px',
                              fontWeight: 600,
                              border: 'none',
                              cursor: 'pointer',
                            }}
                          >
                            Switch To
                          </button>
                        ) : (
                          <span style={{ fontSize: '12px', color: 'var(--accent-indigo)', fontWeight: 600, padding: '6px 10px' }}>
                            Current Context
                          </span>
                        )}

                        <button
                          onClick={() => { setProjectToDelete(proj); setDeleteConfirmName(''); setPurgeExternal(false); }}
                          title="Delete Project"
                          style={{
                            padding: '6px',
                            borderRadius: '6px',
                            backgroundColor: 'rgba(239, 68, 68, 0.1)',
                            border: '1px solid rgba(239, 68, 68, 0.2)',
                            color: '#f87171',
                            cursor: 'pointer',
                          }}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {visibleTab === 'create' && (
            <form onSubmit={handleCreate} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              <div>
                <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, color: '#fff', marginBottom: '6px' }}>
                  Project Name *
                </label>
                <input
                  type="text"
                  required
                  value={newProjName}
                  onChange={(e) => setNewProjName(e.target.value)}
                  placeholder="e.g. obsidian-notes, vector-experiments"
                  style={{
                    width: '100%',
                    padding: '10px 14px',
                    backgroundColor: 'var(--bg-tertiary)',
                    border: '1px solid var(--border-color)',
                    borderRadius: '6px',
                    color: '#fff',
                    fontSize: '13px',
                    boxSizing: 'border-box',
                  }}
                />
              </div>

              <div>
                <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, color: '#fff', marginBottom: '6px' }}>
                  Custom Storage Directory Path (Optional)
                </label>
                <input
                  type="text"
                  value={newProjPath}
                  onChange={(e) => setNewProjPath(e.target.value)}
                  placeholder="Leave blank for default (~/.trace_lite/dbs/<name>)"
                  style={{
                    width: '100%',
                    padding: '10px 14px',
                    backgroundColor: 'var(--bg-tertiary)',
                    border: '1px solid var(--border-color)',
                    borderRadius: '6px',
                    color: '#fff',
                    fontSize: '13px',
                    boxSizing: 'border-box',
                    fontFamily: 'var(--font-mono)',
                  }}
                />
              </div>

              <div>
                <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, color: '#fff', marginBottom: '6px' }}>
                  Description (Optional)
                </label>
                <textarea
                  rows={3}
                  value={newProjDesc}
                  onChange={(e) => setNewProjDesc(e.target.value)}
                  placeholder="Project purpose, dataset summary..."
                  style={{
                    width: '100%',
                    padding: '10px 14px',
                    backgroundColor: 'var(--bg-tertiary)',
                    border: '1px solid var(--border-color)',
                    borderRadius: '6px',
                    color: '#fff',
                    fontSize: '13px',
                    boxSizing: 'border-box',
                    resize: 'vertical',
                  }}
                />
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '10px' }}>
                <button
                  type="button"
                  onClick={() => setActiveTab('list')}
                  style={{
                    padding: '8px 16px',
                    borderRadius: '6px',
                    backgroundColor: 'var(--bg-tertiary)',
                    color: 'var(--text-secondary)',
                    border: '1px solid var(--border-color)',
                    fontSize: '13px',
                    cursor: 'pointer',
                  }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  style={{
                    padding: '8px 20px',
                    borderRadius: '6px',
                    backgroundColor: 'var(--accent-indigo)',
                    color: '#fff',
                    fontSize: '13px',
                    fontWeight: 600,
                    border: 'none',
                    cursor: isSubmitting ? 'not-allowed' : 'pointer',
                  }}
                >
                  {isSubmitting ? 'Creating...' : 'Create & Switch Project'}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};
