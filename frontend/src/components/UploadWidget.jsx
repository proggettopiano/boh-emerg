'use client';

import { useState } from 'react';
import { useUpload } from '@/context/UploadContext';
import { CheckCircle2, AlertCircle, Music, ChevronUp, X } from 'lucide-react';

const COLORS = {
  bg: '#18181b',
  bgHeader: '#09090b',
  border: '#27272a',
  accent: '#e4c97e',
  accentDim: '#a38b4a',
  text: '#fafafa',
  textMuted: '#a1a1aa',
  success: '#4ade80',
  error: '#f87171',
  barBg: '#3f3f46',
  barFill: '#e4c97e',
  barComplete: '#4ade80',
  barError: '#f87171',
};

function barColor(status) {
  if (status === 'complete') return COLORS.barComplete;
  if (status === 'error') return COLORS.barError;
  return COLORS.barFill;
}

function truncate(str, maxLen = 32) {
  return str.length > maxLen ? str.slice(0, maxLen - 1) + '…' : str;
}

function IconMusicNote() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z" />
    </svg>
  );
}

function IconCheck() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function IconX() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function IconError() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="8" x2="12" y2="12" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </svg>
  );
}

function IconChevron({ up }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ transform: up ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s ease' }}
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

function UploadRow({ upload, onDismiss }) {
  const { id, filename, status, progress, message, error } = upload;
  const isActive = status !== 'complete' && status !== 'error';
  const isComplete = status === 'complete';
  const isError = status === 'error';

  return (
    <div
      style={{
        padding: '10px 14px',
        borderBottom: `1px solid ${COLORS.border}`,
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        minWidth: 0,
      }}
    >
      {/* Icon */}
      <div
        style={{
          flexShrink: 0,
          width: 26,
          height: 26,
          borderRadius: '50%',
          background: isComplete ? '#14532d' : isError ? '#450a0a' : '#3f3f46',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: isComplete ? COLORS.success : isError ? COLORS.error : COLORS.accent,
        }}
      >
        {isComplete ? <IconCheck /> : isError ? <IconError /> : <IconMusicNote />}
      </div>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: 500,
            color: isError ? COLORS.error : COLORS.text,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            marginBottom: 4,
            fontFamily: 'system-ui, sans-serif',
          }}
        >
          {truncate(filename)}
        </div>

        <div
          style={{
            height: 4,
            background: COLORS.barBg,
            borderRadius: 99,
            overflow: 'hidden',
            marginBottom: 4,
          }}
        >
          <div
            style={{
              height: '100%',
              width: `${isError ? 100 : progress}%`,
              background: barColor(status),
              borderRadius: 99,
              transition: isActive ? 'width 0.5s ease' : 'none',
            }}
          />
        </div>

        <div
          style={{
            fontSize: 11,
            color: isError ? COLORS.error : COLORS.textMuted,
            fontFamily: 'system-ui, sans-serif',
          }}
        >
          {isError ? error || 'Errore durante l\'elaborazione' : message}
        </div>
      </div>

      {/* Dismiss button */}
      {!isActive && (
        <button
          onClick={() => onDismiss(id)}
          aria-label="Chiudi"
          style={{
            flexShrink: 0,
            width: 22,
            height: 22,
            border: 'none',
            background: 'transparent',
            color: COLORS.textMuted,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 4,
            padding: 0,
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = COLORS.text)}
          onMouseLeave={(e) => (e.currentTarget.style.color = COLORS.textMuted)}
        >
          <IconX />
        </button>
      )}
    </div>
  );
}

export function UploadWidget() {
  const { uploads, dismissUpload } = useUpload();
  const [minimized, setMinimized] = useState(false);

  if (uploads.length === 0) return null;

  const activeCount = uploads.filter((u) => u.status !== 'complete' && u.status !== 'error').length;
  const completeCount = uploads.filter((u) => u.status === 'complete').length;
  const errorCount = uploads.filter((u) => u.status === 'error').length;

  let headerText;
  if (activeCount > 0) {
    headerText = `Caricamento in corso (${activeCount})`;
  } else if (completeCount > 0) {
    headerText = `${completeCount} caricati`;
  } else if (errorCount > 0) {
    headerText = `${errorCount} errori`;
  }

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 20,
        right: 20,
        width: 360,
        maxWidth: 'calc(100vw - 40px)',
        background: COLORS.bg,
        border: `1px solid ${COLORS.border}`,
        borderRadius: 8,
        boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.5)',
        overflow: 'hidden',
        zIndex: 9999,
        fontFamily: 'system-ui, -apple-system, sans-serif',
      }}
    >
      {/* Header */}
      <div
        style={{
          background: COLORS.bgHeader,
          padding: '12px 16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: `1px solid ${COLORS.border}`,
          cursor: 'pointer',
        }}
        onClick={() => setMinimized(!minimized)}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            flex: 1,
          }}
        >
          <Music size={14} style={{ color: COLORS.accent, flexShrink: 0 }} />
          <span style={{ fontSize: 13, fontWeight: 600, color: COLORS.text }}>
            {headerText}
          </span>
        </div>
        <button
          aria-label={minimized ? 'Espandi' : 'Minimizza'}
          onClick={(e) => {
            e.stopPropagation();
            setMinimized(!minimized);
          }}
          style={{
            border: 'none',
            background: 'transparent',
            color: COLORS.textMuted,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 0,
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = COLORS.text)}
          onMouseLeave={(e) => (e.currentTarget.style.color = COLORS.textMuted)}
        >
          <IconChevron up={minimized} />
        </button>
      </div>

      {/* Content */}
      {!minimized && (
        <div
          style={{
            maxHeight: '300px',
            overflowY: 'auto',
          }}
        >
          {uploads.map((upload) => (
            <UploadRow key={upload.id} upload={upload} onDismiss={dismissUpload} />
          ))}
        </div>
      )}
    </div>
  );
}
