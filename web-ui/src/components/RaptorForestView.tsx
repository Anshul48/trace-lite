import React, { useState, useMemo } from 'react';
import { ForestResponse, TreeData, TreeNode } from '../types/api';
import {
  Layers,
  Search,
  Activity,
  Flame,
  Clock,
  Info,
} from 'lucide-react';

interface RaptorForestViewProps {
  forest: ForestResponse | null;
}

export const RaptorForestView: React.FC<RaptorForestViewProps> = ({ forest }) => {
  const [selectedTreeId, setSelectedTreeId] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<TreeNode | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterActiveOnly, setFilterActiveOnly] = useState(false);

  const currentTree: TreeData | null = useMemo(() => {
    if (!forest || forest.trees.length === 0) return null;
    if (!selectedTreeId) return forest.trees[0];
    return forest.trees.find((t) => t.tree_id === selectedTreeId) || forest.trees[0];
  }, [forest, selectedTreeId]);

  // Group nodes by level (level 2, 1, 0, etc.)
  const nodesByLevel = useMemo(() => {
    if (!currentTree) return new Map<number, TreeNode[]>();
    const map = new Map<number, TreeNode[]>();
    currentTree.nodes.forEach((node) => {
      // Filter logic
      if (filterActiveOnly && !node.is_active) return;
      if (searchQuery) {
        const query = searchQuery.toLowerCase();
        const matchesText = node.summary_text.toLowerCase().includes(query);
        const matchesId = node.node_id.toLowerCase().includes(query);
        if (!matchesText && !matchesId) return;
      }

      const list = map.get(node.level) || [];
      list.push(node);
      map.set(node.level, list);
    });
    return map;
  }, [currentTree, searchQuery, filterActiveOnly]);

  const sortedLevels = useMemo(() => {
    return Array.from(nodesByLevel.keys()).sort((a, b) => b - a); // Higher levels at top
  }, [nodesByLevel]);

  if (!forest || forest.trees.length === 0) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
        <Layers size={48} style={{ marginBottom: '12px', opacity: 0.5 }} />
        <h3>No RAPTOR Trees Found</h3>
        <p>Ingest documents to construct RAPTOR hierarchical summary trees in trace-lite cortex.</p>
      </div>
    );
  }

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* Top Toolbar */}
      <div
        className="glass-card"
        style={{
          padding: '16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '16px',
        }}
      >
        {/* Tree Selection Tabs */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase' }}>
            Select Tree:
          </span>
          <div style={{ display: 'flex', gap: '8px' }}>
            {forest.trees.map((t) => (
              <button
                key={t.tree_id}
                onClick={() => {
                  setSelectedTreeId(t.tree_id);
                  setSelectedNode(null);
                }}
                style={{
                  padding: '6px 12px',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '13px',
                  fontWeight: 600,
                  backgroundColor:
                    currentTree?.tree_id === t.tree_id ? 'var(--accent-indigo)' : 'var(--bg-secondary)',
                  color: currentTree?.tree_id === t.tree_id ? '#fff' : 'var(--text-secondary)',
                  border: '1px solid var(--border-color)',
                }}
              >
                {t.name || t.tree_id} ({t.node_count} nodes)
              </button>
            ))}
          </div>
        </div>

        {/* Search & Energy Filters */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{ position: 'relative' }}>
            <Search
              size={14}
              style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }}
            />
            <input
              type="text"
              placeholder="Search nodes by text or ID..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{ paddingLeft: '32px', width: '240px' }}
            />
          </div>

          <button
            onClick={() => setFilterActiveOnly(!filterActiveOnly)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '8px 12px',
              borderRadius: 'var(--radius-sm)',
              fontSize: '12px',
              backgroundColor: filterActiveOnly ? 'rgba(52, 211, 153, 0.2)' : 'var(--bg-secondary)',
              color: filterActiveOnly ? 'var(--accent-green)' : 'var(--text-secondary)',
              border: filterActiveOnly ? '1px solid var(--accent-green)' : '1px solid var(--border-color)',
            }}
          >
            <Activity size={14} />
            {filterActiveOnly ? 'Active Only' : 'All Nodes'}
          </button>
        </div>
      </div>

      {/* Main DAG Layout & Inspector split view */}
      {currentTree && (
        <div style={{ display: 'grid', gridTemplateColumns: selectedNode ? '1fr 360px' : '1fr', gap: '20px' }}>
          {/* DAG Canvas View */}
          <div className="glass-card" style={{ padding: '24px', overflowX: 'auto' }}>
            <div style={{ marginBottom: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h3 style={{ fontSize: '16px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '8px' }}>
                  {currentTree.name}
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                    ID: {currentTree.tree_id}
                  </span>
                </h3>
                <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                  {currentTree.description} • Depth: {currentTree.depth} • Root: {currentTree.root_node_id}
                </p>
              </div>

              <div style={{ display: 'flex', gap: '16px', fontSize: '12px' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: '4px', color: 'var(--accent-green)' }}>
                  ● Active Energy ({'>'}0.5)
                </span>
                <span style={{ display: 'flex', alignItems: 'center', gap: '4px', color: 'var(--accent-rose)' }}>
                  ● Decayed ({'<'}0.5)
                </span>
              </div>
            </div>

            {/* Tree Hierarchy Columns */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
              {sortedLevels.map((level) => {
                const levelNodes = nodesByLevel.get(level) || [];
                return (
                  <div
                    key={level}
                    style={{
                      backgroundColor: 'rgba(18, 24, 36, 0.4)',
                      borderRadius: 'var(--radius-md)',
                      padding: '16px',
                      border: '1px solid var(--border-color)',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        marginBottom: '12px',
                        borderBottom: '1px dashed var(--border-color)',
                        paddingBottom: '8px',
                      }}
                    >
                      <span
                        style={{
                          fontSize: '12px',
                          fontWeight: 700,
                          textTransform: 'uppercase',
                          letterSpacing: '0.5px',
                          color: level === 0 ? 'var(--accent-cyan)' : level === 1 ? 'var(--accent-purple)' : 'var(--accent-amber)',
                        }}
                      >
                        {level === 0 ? '🍃 Leaf Level 0 (Atoms)' : `🌲 Summary Level ${level}`}
                      </span>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                        {levelNodes.length} node{levelNodes.length !== 1 ? 's' : ''}
                      </span>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '12px' }}>
                      {levelNodes.map((node) => {
                        const isSelected = selectedNode?.node_id === node.node_id;
                        const energyClass =
                          node.energy_score >= 0.7
                            ? 'energy-high'
                            : node.energy_score >= 0.4
                            ? 'energy-medium'
                            : 'energy-low';

                        return (
                          <div
                            key={node.node_id}
                            onClick={() => setSelectedNode(node)}
                            className="glass-card-interactive"
                            style={{
                              padding: '12px',
                              cursor: 'pointer',
                              border: isSelected ? '2px solid var(--accent-indigo)' : undefined,
                              boxShadow: isSelected ? '0 0 12px rgba(99, 102, 241, 0.3)' : undefined,
                            }}
                          >
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '8px' }}>
                              <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--accent-indigo)' }}>
                                {node.node_id}
                              </span>
                              <span className={`energy-pill ${energyClass}`}>
                                <Flame size={10} />
                                {node.energy_score}
                              </span>
                            </div>

                            <p
                              style={{
                                fontSize: '12px',
                                color: 'var(--text-primary)',
                                display: '-webkit-box',
                                WebkitLineClamp: 3,
                                WebkitBoxOrient: 'vertical',
                                overflow: 'hidden',
                                lineHeight: '1.4',
                                marginBottom: '10px',
                              }}
                            >
                              {node.summary_text}
                            </p>

                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: 'var(--text-muted)' }}>
                              <span>
                                {node.child_node_ids.length > 0 ? `Children: ${node.child_node_ids.length}` : `Atoms: ${node.atom_ids.length}`}
                              </span>
                              <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                <Clock size={10} /> Access: {node.access_count}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Selected Node Details Drawer */}
          {selectedNode && (
            <div className="glass-card" style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border-color)', paddingBottom: '12px' }}>
                <h4 style={{ fontSize: '14px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <Info size={16} color="var(--accent-indigo)" />
                  Node Inspector
                </h4>
                <button
                  onClick={() => setSelectedNode(null)}
                  style={{ fontSize: '18px', color: 'var(--text-muted)', lineHeight: '1' }}
                >
                  ×
                </button>
              </div>

              <div>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>NODE ID</div>
                <div style={{ fontSize: '13px', fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--accent-cyan)' }}>
                  {selectedNode.node_id}
                </div>
              </div>

              <div>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>SUMMARY TEXT</div>
                <div
                  style={{
                    fontSize: '12px',
                    padding: '10px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: 'var(--bg-secondary)',
                    border: '1px solid var(--border-color)',
                    maxHeight: '140px',
                    overflowY: 'auto',
                  }}
                >
                  {selectedNode.summary_text}
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                <div style={{ backgroundColor: 'var(--bg-secondary)', padding: '8px', borderRadius: 'var(--radius-sm)' }}>
                  <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>LEVEL</div>
                  <div style={{ fontSize: '14px', fontWeight: 700 }}>{selectedNode.level}</div>
                </div>

                <div style={{ backgroundColor: 'var(--bg-secondary)', padding: '8px', borderRadius: 'var(--radius-sm)' }}>
                  <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>ENERGY SCORE</div>
                  <div style={{ fontSize: '14px', fontWeight: 700, color: 'var(--accent-amber)' }}>{selectedNode.energy_score}</div>
                </div>

                <div style={{ backgroundColor: 'var(--bg-secondary)', padding: '8px', borderRadius: 'var(--radius-sm)' }}>
                  <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>ACCESS COUNT</div>
                  <div style={{ fontSize: '14px', fontWeight: 700 }}>{selectedNode.access_count}</div>
                </div>

                <div style={{ backgroundColor: 'var(--bg-secondary)', padding: '8px', borderRadius: 'var(--radius-sm)' }}>
                  <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>STATUS</div>
                  <div style={{ fontSize: '12px', fontWeight: 700, color: selectedNode.is_active ? 'var(--accent-green)' : 'var(--accent-rose)' }}>
                    {selectedNode.is_active ? 'Active' : 'Decayed'}
                  </div>
                </div>
              </div>

              {selectedNode.parent_node_id && (
                <div>
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>PARENT NODE</div>
                  <div
                    style={{
                      fontSize: '11px',
                      fontFamily: 'var(--font-mono)',
                      padding: '6px 8px',
                      backgroundColor: 'var(--bg-secondary)',
                      borderRadius: 'var(--radius-sm)',
                      color: 'var(--accent-purple)',
                    }}
                  >
                    {selectedNode.parent_node_id}
                  </div>
                </div>
              )}

              {selectedNode.child_node_ids.length > 0 && (
                <div>
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>CHILD NODES</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', maxHeight: '100px', overflowY: 'auto' }}>
                    {selectedNode.child_node_ids.map((childId) => (
                      <div
                        key={childId}
                        style={{
                          fontSize: '11px',
                          fontFamily: 'var(--font-mono)',
                          padding: '4px 8px',
                          backgroundColor: 'var(--bg-secondary)',
                          borderRadius: 'var(--radius-sm)',
                        }}
                      >
                        {childId}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {selectedNode.atom_ids.length > 0 && (
                <div>
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>BOUND ATOM IDS</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                    {selectedNode.atom_ids.map((atomId) => (
                      <span
                        key={atomId}
                        style={{
                          fontSize: '10px',
                          fontFamily: 'var(--font-mono)',
                          padding: '2px 6px',
                          backgroundColor: 'rgba(56, 189, 248, 0.15)',
                          color: 'var(--accent-cyan)',
                          borderRadius: '4px',
                          border: '1px solid rgba(56, 189, 248, 0.3)',
                        }}
                      >
                        {atomId}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
