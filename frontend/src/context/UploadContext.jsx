'use client';

import { createContext, useContext, useCallback, useReducer } from 'react';

const REMOVE_COMPLETED_AFTER_MS = 8000;

function uploadsReducer(state, action) {
  switch (action.type) {
    case 'ADD':
      return [...state, action.upload];

    case 'UPDATE':
      return state.map(u =>
        u.id === action.id ? { ...u, ...action.updates } : u
      );

    case 'REMOVE':
      return state.filter(u => u.id !== action.id);

    default:
      return state;
  }
}

const UploadContext = createContext(null);

export function UploadProvider({ children }) {
  const [uploads, dispatch] = useReducer(uploadsReducer, []);

  /**
   * Traccia un upload nel widget.
   * Questo è solo UI orchestration - il polling dello stato è centralizzato in PdfStateContext.
   */
  const trackUpload = useCallback((uploadId, filename) => {
    dispatch({
      type: 'ADD',
      upload: {
        id: uploadId,
        filename: filename || 'Spartito.pdf',
        status: 'uploading',
        progress: 0,
        message: 'Invio al server...',
        error: null,
      },
    });
  }, []);

  /**
   * Aggiorna lo stato di un upload in corso.
   */
  const updateUpload = useCallback((id, updates) => {
    dispatch({
      type: 'UPDATE',
      id,
      updates,
    });
  }, []);

  /**
   * Segna un upload come iniziato il processing (dopo che il file è caricato).
   * Il polling dello stato è gestito CENTRALMENTE dal PdfStateContext.
   */
  const startProcessing = useCallback((id, pdfId) => {
    dispatch({
      type: 'UPDATE',
      id,
      updates: {
        status: 'processing',
        progress: 15,
        message: 'Elaborazione PDF avviata...',
        pdfId, // store per riferimento
      },
    });
  }, []);

  /**
   * Segnala un errore di upload (non è uno stato finale - rimuove dopo 8s).
   */
  const errorUpload = useCallback((id, error) => {
    dispatch({
      type: 'UPDATE',
      id,
      updates: {
        status: 'error',
        message: error || 'Errore durante l\'upload',
        error,
      },
    });

    setTimeout(() => {
      dispatch({ type: 'REMOVE', id });
    }, REMOVE_COMPLETED_AFTER_MS);
  }, []);

  /**
   * Rimuove un upload dalla lista (pulsante X nel widget).
   */
  const dismissUpload = useCallback((id) => {
    dispatch({ type: 'REMOVE', id });
  }, []);

  return (
    <UploadContext.Provider value={{
      uploads,
      trackUpload,
      updateUpload,
      startProcessing,
      errorUpload,
      dismissUpload,
    }}>
      {children}
    </UploadContext.Provider>
  );
}

/**
 * Hook per accedere al context di upload.
 */
export function useUpload() {
  const ctx = useContext(UploadContext);
  if (!ctx) {
    throw new Error('useUpload deve essere usato dentro un <UploadProvider>');
  }
  return ctx;
}
