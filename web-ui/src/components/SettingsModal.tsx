import React, { useState, useEffect } from 'react';
import {
  X,
  Settings,
  Cpu,
  Key,
  Globe,
  Download,
  Eye,
  EyeOff,
} from 'lucide-react';
import { ConfigResponse, AvailableProviderInfo, ModelStatusResponse } from '../types/api';
import { saveConfig, getExportUrl, setAutoLoadModels } from '../services/api';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  configData: ConfigResponse | null;
  modelStatus: ModelStatusResponse | null;
  onConfigUpdated: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  configData,
  modelStatus,
  onConfigUpdated,
}) => {
  const [selectedProviderId, setSelectedProviderId] = useState<string>('ollama');
  const [model, setModel] = useState<string>('');
  const [apiKey, setApiKey] = useState<string>('');
  const [apiBase, setApiBase] = useState<string>('');
  const [apiVersion, setApiVersion] = useState<string>('');
  const [showApiKey, setShowApiKey] = useState<boolean>(false);

  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [autoLoadModels, setAutoLoadModelsState] = useState<boolean>(true);
  const [isAutoLoadSaving, setIsAutoLoadSaving] = useState<boolean>(false);

  useEffect(() => {
    if (configData) {
      setAutoLoadModelsState(configData.auto_load_models !== false);
      const activeId = configData.active_provider || 'ollama';
      setSelectedProviderId(activeId);
      const savedInfo = configData.saved_providers[activeId];
      if (savedInfo) {
        setModel(savedInfo.model || configData.active_model || '');
        setApiKey('');
        setApiBase(savedInfo.api_base || configData.api_base || '');
        setApiVersion(savedInfo.api_version || configData.api_version || '');
      } else {
        const provDef = configData.available_providers.find((p) => p.id === activeId);
        setModel(provDef?.default_model || '');
        setApiKey('');
        setApiBase(provDef?.default_api_base || '');
        setApiVersion(provDef?.default_api_version || '');
      }
    }
  }, [configData]);

  if (!isOpen) return null;

  const currentProviderDef: AvailableProviderInfo | undefined = configData?.available_providers.find(
    (p) => p.id === selectedProviderId
  );

  const handleProviderChange = (providerId: string) => {
    setSelectedProviderId(providerId);
    setStatusMsg(null);
    const saved = configData?.saved_providers[providerId];
    const def = configData?.available_providers.find((p) => p.id === providerId);
    if (saved) {
      setModel(saved.model || def?.default_model || '');
      setApiKey('');
      setApiBase(saved.api_base || def?.default_api_base || '');
      setApiVersion(saved.api_version || def?.default_api_version || '');
    } else {
      setModel(def?.default_model || '');
      setApiKey('');
      setApiBase(def?.default_api_base || '');
      setApiVersion(def?.default_api_version || '');
    }
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSaving(true);
    setStatusMsg(null);
    try {
      await saveConfig(selectedProviderId, apiKey || undefined, model || undefined, apiBase || undefined, apiVersion || undefined);
      setApiKey('');
      setStatusMsg('Verified and activated. The key is stored in the system credential store.');
      onConfigUpdated();
    } catch (err: any) {
      setStatusMsg(`Save & Verify failed; the previous provider remains active. ${err.message}`);
    } finally {
      setIsSaving(false);
    }
  };

  const handleAutoLoadChange = async (enabled: boolean) => {
    setAutoLoadModelsState(enabled);
    setIsAutoLoadSaving(true);
    try {
      await setAutoLoadModels(enabled);
      onConfigUpdated();
    } catch (err: any) {
      setAutoLoadModelsState(!enabled);
      setStatusMsg(`Model warm-up setting failed: ${err.message}`);
    } finally {
      setIsAutoLoadSaving(false);
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
          maxWidth: '720px',
          backgroundColor: 'var(--bg-secondary)',
          border: '1px solid var(--border-color)',
          borderRadius: 'var(--radius-lg, 12px)',
          boxShadow: '0 20px 40px rgba(0,0,0,0.5)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          maxHeight: '90vh',
        }}
      >
        {/* Header */}
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
            <Settings size={20} color="var(--accent-purple)" />
            <h2 style={{ fontSize: '16px', fontWeight: 600, margin: 0, color: '#fff' }}>
              LLM Provider & System Settings
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

        {/* Content Body */}
        <div style={{ padding: '20px 24px', overflowY: 'auto', flex: 1 }}>
          <form onSubmit={handleSave} style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
            <div style={{ backgroundColor: 'var(--bg-tertiary)', padding: '14px 16px', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '9px', color: '#fff', fontSize: '13px', fontWeight: 600 }}>
                <input
                  type="checkbox"
                  checked={autoLoadModels}
                  disabled={isAutoLoadSaving}
                  onChange={(event) => void handleAutoLoadChange(event.target.checked)}
                />
                Preload embedding model on startup
              </label>
              <div style={{ marginTop: '7px', color: 'var(--text-muted)', fontSize: '11px' }}>
                {modelStatus?.status === 'loading' && 'Loading the local embedding model…'}
                {modelStatus?.status === 'ready' && 'Embedding model ready.'}
                {modelStatus?.status === 'failed' && `Embedding model failed: ${modelStatus.error || 'check terminal diagnostics.'}`}
                {(!modelStatus || modelStatus.status === 'not_loaded') && (autoLoadModels
                  ? 'Long-running UI, chat, and watch commands load it in the background.'
                  : 'Automatic warm-up is disabled; the model remains lazy.')}
              </div>
            </div>
            {/* Provider Selection */}
            <div>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: '#fff', marginBottom: '8px' }}>
                Select Active LLM Provider
              </label>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '10px' }}>
                {configData?.available_providers.map((p) => {
                  const isSelected = p.id === selectedProviderId;
                  const isCurrentActive = p.id === configData.active_provider;
                  return (
                    <div
                      key={p.id}
                      onClick={() => handleProviderChange(p.id)}
                      style={{
                        padding: '12px',
                        borderRadius: '8px',
                        backgroundColor: isSelected ? 'rgba(168, 85, 247, 0.12)' : 'var(--bg-tertiary)',
                        border: isSelected ? '1px solid var(--accent-purple)' : '1px solid var(--border-color)',
                        cursor: 'pointer',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '4px',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <span style={{ fontWeight: 600, fontSize: '13px', color: '#fff' }}>{p.name}</span>
                        {isCurrentActive && (
                          <span style={{ fontSize: '9px', fontWeight: 700, padding: '2px 4px', borderRadius: '4px', backgroundColor: 'rgba(34,197,94,0.2)', color: 'var(--accent-green)' }}>
                            ACTIVE
                          </span>
                        )}
                      </div>
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)', lineHeight: '1.3' }}>
                        {p.description}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Model & Endpoint Configuration Form */}
            <div style={{ backgroundColor: 'var(--bg-tertiary)', padding: '16px', borderRadius: '8px', border: '1px solid var(--border-color)', display: 'flex', flexDirection: 'column', gap: '14px' }}>
              <div>
                <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', fontWeight: 500, color: '#fff', marginBottom: '6px' }}>
                  <Cpu size={14} color="var(--accent-cyan)" /> Active Model Name / ID
                </label>
                <input
                  type="text"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder={currentProviderDef?.default_model || 'e.g. gpt-4o-mini'}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    backgroundColor: 'var(--bg-primary)',
                    border: '1px solid var(--border-color)',
                    borderRadius: '6px',
                    color: '#fff',
                    fontSize: '13px',
                    fontFamily: 'var(--font-mono)',
                    boxSizing: 'border-box',
                  }}
                />
                {currentProviderDef?.popular_models && currentProviderDef.popular_models.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '6px' }}>
                    <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Presets:</span>
                    {currentProviderDef.popular_models.map((pm) => (
                      <button
                        key={pm}
                        type="button"
                        onClick={() => setModel(pm)}
                        style={{
                          fontSize: '11px',
                          padding: '2px 6px',
                          borderRadius: '4px',
                          backgroundColor: 'var(--bg-secondary)',
                          color: 'var(--accent-cyan)',
                          border: '1px solid var(--border-color)',
                          cursor: 'pointer',
                          fontFamily: 'var(--font-mono)',
                        }}
                      >
                        {pm}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {currentProviderDef?.requires_api_key && (
                <div>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', fontWeight: 500, color: '#fff', marginBottom: '6px' }}>
                    <Key size={14} color="var(--accent-amber)" /> API Key
                  </label>
                  <div style={{ position: 'relative' }}>
                    <input
                      type={showApiKey ? 'text' : 'password'}
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                      placeholder="sk-..."
                      style={{
                        width: '100%',
                        padding: '8px 36px 8px 12px',
                        backgroundColor: 'var(--bg-primary)',
                        border: '1px solid var(--border-color)',
                        borderRadius: '6px',
                        color: '#fff',
                        fontSize: '13px',
                        fontFamily: 'var(--font-mono)',
                        boxSizing: 'border-box',
                      }}
                    />
                    <button
                      type="button"
                      onClick={() => setShowApiKey(!showApiKey)}
                      style={{
                        position: 'absolute',
                        right: '8px',
                        top: '50%',
                        transform: 'translateY(-50%)',
                        background: 'none',
                        border: 'none',
                        color: 'var(--text-muted)',
                        cursor: 'pointer',
                      }}
                    >
                      {showApiKey ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                </div>
              )}

              {(currentProviderDef?.requires_api_base || apiBase) && (
                <div>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', fontWeight: 500, color: '#fff', marginBottom: '6px' }}>
                    <Globe size={14} color="var(--accent-green)" /> Custom API Base URL
                  </label>
                  <input
                    type="text"
                    value={apiBase}
                    onChange={(e) => setApiBase(e.target.value)}
                    placeholder={currentProviderDef?.default_api_base || 'http://localhost:11434'}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      backgroundColor: 'var(--bg-primary)',
                      border: '1px solid var(--border-color)',
                      borderRadius: '6px',
                      color: '#fff',
                      fontSize: '13px',
                      fontFamily: 'var(--font-mono)',
                      boxSizing: 'border-box',
                    }}
                  />
                </div>
              )}

              {currentProviderDef?.requires_api_version && (
                <div>
                  <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, color: '#fff', marginBottom: '6px' }}>Azure API Version</label>
                  <input type="text" value={apiVersion} onChange={(e) => setApiVersion(e.target.value)} placeholder={currentProviderDef.default_api_version || '2024-10-21'} style={{ width: '100%', padding: '8px 12px', backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border-color)', borderRadius: '6px', color: '#fff', fontSize: '13px', boxSizing: 'border-box' }} />
                </div>
              )}
            </div>

            {statusMsg && (
              <div style={{ fontSize: '13px', fontWeight: 500, color: statusMsg.startsWith('Verified') ? '#4ade80' : '#f87171' }}>
                {statusMsg}
              </div>
            )}

            {/* Modal Actions */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderTop: '1px solid var(--border-color)', paddingTop: '16px' }}>
              <a
                href={getExportUrl()}
                download
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                  padding: '8px 14px',
                  borderRadius: '6px',
                  backgroundColor: 'var(--bg-tertiary)',
                  border: '1px solid var(--border-color)',
                  color: 'var(--text-secondary)',
                  fontSize: '12px',
                  fontWeight: 500,
                  textDecoration: 'none',
                }}
              >
                <Download size={14} /> Export DB Zip Archive
              </a>

              <div style={{ display: 'flex', gap: '10px' }}>
                <button
                  type="submit"
                  disabled={isSaving}
                  style={{
                    padding: '8px 20px',
                    borderRadius: '6px',
                    backgroundColor: 'var(--accent-purple)',
                    color: '#fff',
                    fontSize: '13px',
                    fontWeight: 600,
                    border: 'none',
                    cursor: isSaving ? 'not-allowed' : 'pointer',
                  }}
                >
                  {isSaving ? 'Saving & verifying...' : 'Save & Verify'}
                </button>
              </div>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
};
