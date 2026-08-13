import React, { useEffect, useRef, useState, useMemo } from 'react';
import * as THREE from 'three';
import { VectorsResponse, VectorPoint } from '../types/api';
import { Box, RotateCw, Sparkles } from 'lucide-react';

interface VectorExplorer3DProps {
  vectors: VectorsResponse | null;
}

export const VectorExplorer3D: React.FC<VectorExplorer3DProps> = ({ vectors }) => {
  const mountRef = useRef<HTMLDivElement>(null);
  const [selectedPoint, setSelectedPoint] = useState<VectorPoint | null>(null);
  const [hoveredPoint, setHoveredPoint] = useState<VectorPoint | null>(null);
  const [colorMode, setColorMode] = useState<'tree' | 'level'>('tree');
  const [autoRotate, setAutoRotate] = useState(true);

  const pointsList = useMemo(() => vectors?.points || [], [vectors]);

  // Color mapping helpers
  const treeColors = useMemo(() => {
    const palette = ['#38bdf8', '#a855f7', '#34d399', '#fbbf24', '#f43f5e', '#6366f1'];
    const map = new Map<string, string>();
    let idx = 0;
    pointsList.forEach((p) => {
      if (!map.has(p.tree_id)) {
        map.set(p.tree_id, palette[idx % palette.length]);
        idx++;
      }
    });
    return map;
  }, [pointsList]);

  const levelColors: Record<number, string> = {
    0: '#38bdf8', // Leaf - Cyan
    1: '#a855f7', // Summary Level 1 - Purple
    2: '#34d399', // Summary Level 2 - Green
    3: '#fbbf24', // Root - Amber
  };

  const getPointColor = (p: VectorPoint): string => {
    if (colorMode === 'tree') {
      return treeColors.get(p.tree_id) || '#6366f1';
    }
    return levelColors[p.level] || '#6366f1';
  };

  useEffect(() => {
    if (!mountRef.current || pointsList.length === 0) return;

    const width = mountRef.current.clientWidth;
    const height = mountRef.current.clientHeight || 500;

    // 1. Scene & Camera
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0d14);

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    camera.position.set(0, 0, 24);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(window.devicePixelRatio);

    mountRef.current.innerHTML = '';
    mountRef.current.appendChild(renderer.domElement);

    // 2. Lighting & Grid Helper
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
    scene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(10, 15, 10);
    scene.add(dirLight);

    const grid = new THREE.GridHelper(30, 20, 0x232d42, 0x121824);
    grid.position.y = -6;
    scene.add(grid);

    // 3. Points Container Object Group
    const group = new THREE.Group();
    scene.add(group);

    // Create 3D Spheres for each vector point
    const meshes: THREE.Mesh[] = [];
    pointsList.forEach((p) => {
      const geometry = new THREE.SphereGeometry(0.4, 16, 16);
      const colorHex = getPointColor(p);
      const material = new THREE.MeshStandardMaterial({
        color: new THREE.Color(colorHex),
        roughness: 0.3,
        metalness: 0.2,
        emissive: new THREE.Color(colorHex),
        emissiveIntensity: 0.2,
      });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(p.x, p.y, p.z);
      mesh.userData = p;
      group.add(mesh);
      meshes.push(mesh);
    });

    // 4. Mouse Rotation & Interactivity
    let isDragging = false;
    let previousMousePosition = { x: 0, y: 0 };
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();

    const onMouseDown = (e: MouseEvent) => {
      isDragging = true;
      previousMousePosition = { x: e.clientX, y: e.clientY };
    };

    const onMouseMove = (e: MouseEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

      // Raycasting for hover state
      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(meshes);
      if (intersects.length > 0) {
        setHoveredPoint(intersects[0].object.userData as VectorPoint);
      } else {
        setHoveredPoint(null);
      }

      if (isDragging) {
        const deltaMove = {
          x: e.clientX - previousMousePosition.x,
          y: e.clientY - previousMousePosition.y,
        };

        group.rotation.y += deltaMove.x * 0.005;
        group.rotation.x += deltaMove.y * 0.005;

        previousMousePosition = { x: e.clientX, y: e.clientY };
      }
    };

    const onMouseUp = () => {
      isDragging = false;
    };

    const onClick = (e: MouseEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(meshes);
      if (intersects.length > 0) {
        setSelectedPoint(intersects[0].object.userData as VectorPoint);
      }
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      camera.position.z += e.deltaY * 0.015;
      camera.position.z = Math.max(5, Math.min(60, camera.position.z));
    };

    const domElem = renderer.domElement;
    domElem.addEventListener('mousedown', onMouseDown);
    domElem.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    domElem.addEventListener('click', onClick);
    domElem.addEventListener('wheel', onWheel, { passive: false });

    // 5. Animation Loop
    let reqId: number;
    const animate = () => {
      reqId = requestAnimationFrame(animate);
      if (autoRotate && !isDragging) {
        group.rotation.y += 0.003;
      }
      renderer.render(scene, camera);
    };
    animate();

    // Resize Handler
    const handleResize = () => {
      if (!mountRef.current) return;
      const w = mountRef.current.clientWidth;
      const h = mountRef.current.clientHeight || 500;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener('resize', handleResize);

    return () => {
      cancelAnimationFrame(reqId);
      window.removeEventListener('resize', handleResize);
      domElem.removeEventListener('mousedown', onMouseDown);
      domElem.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      domElem.removeEventListener('click', onClick);
      domElem.removeEventListener('wheel', onWheel);
      if (mountRef.current) mountRef.current.innerHTML = '';
      renderer.dispose();
    };
  }, [pointsList, colorMode, autoRotate]);

  if (!vectors || !vectors.available || vectors.count === 0) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
        <Box size={48} style={{ marginBottom: '12px', opacity: 0.5 }} />
        <h3>{vectors?.diagnostic?.code === 'empty' ? 'No Vector Embeddings Available' : 'Vector Explorer Unavailable'}</h3>
        <p>{vectors?.diagnostic?.message || 'Build tree nodes to generate 2D/3D projection points.'}</p>
      </div>
    );
  }

  return (
    <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* 3D Toolbar */}
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
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <h3 style={{ fontSize: '15px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Box size={18} color="var(--accent-purple)" />
            Vector Point-Cloud ({vectors.displayed_count} of {vectors.total_count} points)
          </h3>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
            Projection: {vectors.projection_method}{vectors.sampled ? ' · deterministic stratified sample' : ''}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Color By:</span>
            <button
              onClick={() => setColorMode('tree')}
              style={{
                padding: '4px 10px',
                borderRadius: 'var(--radius-sm)',
                fontSize: '12px',
                fontWeight: 600,
                backgroundColor: colorMode === 'tree' ? 'var(--accent-purple)' : 'var(--bg-secondary)',
                color: colorMode === 'tree' ? '#fff' : 'var(--text-secondary)',
                border: '1px solid var(--border-color)',
              }}
            >
              Tree ID
            </button>
            <button
              onClick={() => setColorMode('level')}
              style={{
                padding: '4px 10px',
                borderRadius: 'var(--radius-sm)',
                fontSize: '12px',
                fontWeight: 600,
                backgroundColor: colorMode === 'level' ? 'var(--accent-cyan)' : 'var(--bg-secondary)',
                color: colorMode === 'level' ? '#fff' : 'var(--text-secondary)',
                border: '1px solid var(--border-color)',
              }}
            >
              Hierarchy Level
            </button>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <button
            onClick={() => setAutoRotate(!autoRotate)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 12px',
              borderRadius: 'var(--radius-sm)',
              fontSize: '12px',
              backgroundColor: autoRotate ? 'rgba(168, 85, 247, 0.2)' : 'var(--bg-secondary)',
              color: autoRotate ? 'var(--accent-purple)' : 'var(--text-secondary)',
              border: autoRotate ? '1px solid var(--accent-purple)' : '1px solid var(--border-color)',
            }}
          >
            <RotateCw size={14} className={autoRotate ? 'spin' : ''} />
            {autoRotate ? 'Auto-Rotate ON' : 'Paused'}
          </button>

          <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            💡 <span style={{ fontWeight: 500 }}>Controls:</span> Drag to orbit • Scroll to zoom • Click node to inspect
          </div>
        </div>
      </div>

      {/* 3D Canvas Canvas & Side Inspector */}
      <div style={{ display: 'grid', gridTemplateColumns: selectedPoint || hoveredPoint ? '1fr 340px' : '1fr', gap: '20px' }}>
        <div
          className="glass-card"
          style={{
            position: 'relative',
            height: '560px',
            borderRadius: 'var(--radius-md)',
            overflow: 'hidden',
          }}
        >
          <div ref={mountRef} style={{ width: '100%', height: '100%' }} />

          {/* Floating Hover Indicator */}
          {hoveredPoint && !selectedPoint && (
            <div
              style={{
                position: 'absolute',
                top: '16px',
                left: '16px',
                backgroundColor: 'rgba(18, 24, 36, 0.9)',
                border: '1px solid var(--accent-purple)',
                padding: '8px 14px',
                borderRadius: 'var(--radius-sm)',
                pointerEvents: 'none',
                maxWidth: '300px',
              }}
            >
              <div style={{ fontSize: '11px', color: 'var(--accent-purple)', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
                {hoveredPoint.id}
              </div>
              <div style={{ fontSize: '12px', color: '#fff', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {hoveredPoint.label}
              </div>
            </div>
          )}
        </div>

        {/* Selected Point Side Inspector */}
        {(selectedPoint || hoveredPoint) && (
          <div className="glass-card" style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border-color)', paddingBottom: '12px' }}>
              <h4 style={{ fontSize: '14px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '6px' }}>
                <Sparkles size={16} color="var(--accent-purple)" />
                Vector Inspector
              </h4>
              {selectedPoint && (
                <button onClick={() => setSelectedPoint(null)} style={{ fontSize: '18px', color: 'var(--text-muted)' }}>
                  ×
                </button>
              )}
            </div>

            {(() => {
              const pt = selectedPoint || hoveredPoint!;
              return (
                <>
                  <div>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>NODE ID</div>
                    <div style={{ fontSize: '13px', fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--accent-cyan)' }}>
                      {pt.id}
                    </div>
                  </div>

                  <div>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>LABEL SUMMARY</div>
                    <div
                      style={{
                        fontSize: '12px',
                        padding: '10px',
                        backgroundColor: 'var(--bg-secondary)',
                        borderRadius: 'var(--radius-sm)',
                        border: '1px solid var(--border-color)',
                      }}
                    >
                      {pt.label}
                    </div>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                    <div style={{ backgroundColor: 'var(--bg-secondary)', padding: '8px', borderRadius: 'var(--radius-sm)' }}>
                      <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>TREE ID</div>
                      <div style={{ fontSize: '12px', fontWeight: 600, color: treeColors.get(pt.tree_id) }}>
                        {pt.tree_id}
                      </div>
                    </div>

                    <div style={{ backgroundColor: 'var(--bg-secondary)', padding: '8px', borderRadius: 'var(--radius-sm)' }}>
                      <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>LEVEL</div>
                      <div style={{ fontSize: '12px', fontWeight: 600 }}>{pt.level}</div>
                    </div>
                  </div>

                  <div>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '6px' }}>3D PROJECTION COORDINATES</div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '6px' }}>
                      <div style={{ backgroundColor: 'var(--bg-secondary)', padding: '6px', textAlign: 'center', borderRadius: '4px', fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
                        X: {pt.x.toFixed(2)}
                      </div>
                      <div style={{ backgroundColor: 'var(--bg-secondary)', padding: '6px', textAlign: 'center', borderRadius: '4px', fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
                        Y: {pt.y.toFixed(2)}
                      </div>
                      <div style={{ backgroundColor: 'var(--bg-secondary)', padding: '6px', textAlign: 'center', borderRadius: '4px', fontFamily: 'var(--font-mono)', fontSize: '11px' }}>
                        Z: {pt.z.toFixed(2)}
                      </div>
                    </div>
                  </div>
                </>
              );
            })()}
          </div>
        )}
      </div>
    </div>
  );
};
