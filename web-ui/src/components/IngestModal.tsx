import React, { useState } from 'react';
import { AlertCircle, CheckCircle2, FileText, FolderSync, Loader2, Upload, X } from 'lucide-react';
import { ingestFile, ingestText, syncVault, uploadSource } from '../services/api';

interface Props { isOpen: boolean; onClose: () => void; onSuccess: () => void; allowServerPaths?: boolean; }

export const IngestModal: React.FC<Props> = ({ isOpen, onClose, onSuccess, allowServerPaths = true }) => {
  const [tab, setTab] = useState<'text' | 'upload' | 'path' | 'folder'>('text');
  const [text, setText] = useState('');
  const [name, setName] = useState('');
  const [filePath, setFilePath] = useState('');
  const [folderPath, setFolderPath] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [organizeAfterSync, setOrganizeAfterSync] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  if (!isOpen) return null;
  const selectTab = (next: typeof tab) => { setTab(next); setError(null); setResult(null); };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true); setError(null); setResult(null);
    try {
      if (tab === 'text') {
        if (!text.trim()) throw new Error('Paste some text before adding a source.');
        const response = await ingestText(text, name || undefined);
        setResult(`${response.atom_count} atom(s) saved. Content is ready to organize.`);
        setText(''); setName('');
      } else if (tab === 'upload') {
        if (!file) throw new Error('Choose a file to upload.');
        const response = await uploadSource(file, name || undefined);
        setResult(`${response.atom_count} atom(s) saved from ${file.name}. Content is ready to organize.`);
        setFile(null); setName('');
      } else if (tab === 'path') {
        if (!filePath.trim()) throw new Error('Enter a server file path.');
        const response = await ingestFile(filePath, name || undefined);
        setResult(`${response.atom_count} atom(s) saved from the server file.`);
        setFilePath(''); setName('');
      } else {
        if (!folderPath.trim()) throw new Error('Enter a server folder path.');
        const response = await syncVault(folderPath, organizeAfterSync);
        setResult(`${response.files_ingested} changed file(s) saved; ${response.pending_atoms ?? 0} atom(s) are ready to organize.`);
      }
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Source operation failed.');
    } finally { setBusy(false); }
  };

  return (
    <div className="modal-scrim" onMouseDown={onClose}>
      <div className="source-modal card" role="dialog" aria-modal="true" aria-labelledby="source-dialog-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="modal-heading"><div><p className="eyebrow">Sources</p><h2 id="source-dialog-title">Add content</h2></div><button className="icon-button" onClick={onClose} aria-label="Close"><X size={18} /></button></div>
        <div className="source-tabs" role="tablist" aria-label="Source type">
          <button className={tab === 'text' ? 'is-active' : ''} onClick={() => selectTab('text')} role="tab" aria-selected={tab === 'text'}><FileText size={15} /> Paste text</button>
          <button className={tab === 'upload' ? 'is-active' : ''} onClick={() => selectTab('upload')} role="tab" aria-selected={tab === 'upload'}><Upload size={15} /> Upload file</button>
          {allowServerPaths && <button className={tab === 'path' ? 'is-active' : ''} onClick={() => selectTab('path')} role="tab" aria-selected={tab === 'path'}>Server file</button>}
          {allowServerPaths && <button className={tab === 'folder' ? 'is-active' : ''} onClick={() => selectTab('folder')} role="tab" aria-selected={tab === 'folder'}><FolderSync size={15} /> Folder</button>}
        </div>
        <form onSubmit={submit} className="source-form">
          {(tab === 'text' || tab === 'upload') && <label>Document name <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Optional title" /></label>}
          {tab === 'text' && <label>Content <textarea rows={8} value={text} onChange={(event) => setText(event.target.value)} placeholder="Paste a note, transcript, or markdown…" autoFocus /></label>}
          {tab === 'upload' && <label className="file-drop">Choose a file<input type="file" accept=".txt,.md,.markdown,text/plain,text/markdown" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />{file ? <strong>{file.name}</strong> : <span>Text and Markdown files are read in the browser, then saved as a source.</span>}</label>}
          {tab === 'path' && <><label>Server file path<input value={filePath} onChange={(event) => setFilePath(event.target.value)} placeholder="C:\\notes\\today.md" /></label><p className="form-hint">Available only when the server is loopback-bound.</p></>}
          {tab === 'folder' && <><label>Connected folder path<input value={folderPath} onChange={(event) => setFolderPath(event.target.value)} placeholder="C:\\Users\\you\\Notes" /></label><label className="check-row"><input type="checkbox" checked={organizeAfterSync} onChange={(event) => setOrganizeAfterSync(event.target.checked)} /> Organize once after this sync</label><p className="form-hint">Continuous watchers queue changes by default; unchanged files are skipped across restarts.</p></>}
          {error && <div className="error-state" role="alert"><AlertCircle size={16} /> {error}</div>}
          {result && <div className="success-state" role="status"><CheckCircle2 size={16} /> {result}</div>}
          <div className="modal-actions"><button type="button" className="secondary-button" onClick={onClose}>Close</button><button className="primary-button" type="submit" disabled={busy}>{busy && <Loader2 size={15} className="spin" />}{busy ? 'Saving…' : tab === 'folder' ? 'Sync folder' : 'Save source'}</button></div>
        </form>
      </div>
    </div>
  );
};
