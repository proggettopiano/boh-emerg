'use client';

import { createContext, useContext, useCallback, useReducer, useRef, useEffect } from 'react';
import api from '@/lib/api';

const POLL_INTERVAL_MS = 2000;

/**
 * PdfStateContext: Single Source of Truth per lo stato dei PDF
 *
 * Mantiene cache dello stato di OGNI PDF in elaborazione.
 * Un unico polling layer per tutta l'app - viewer e upload si iscrivono solo in lettura.
 *
 * Mappa interna:
 * {
 *   'pdf-id-1': {
 *     id: 'pdf-id-1',
 *     processing_status: 'ready' | 'queued' | 'processing' | 'failed' | 'uploading',
 *     processing_error: null | 'error message',
 *     pages: 42,
 *     ... (altri campi dal backend)
 *   }
 * }
 */

function pdfStateReducer(state, action) {
  switch (action.type) {
    case 'SET_PDF':
      return {
        ...state,
        [action.pdfId]: action.data,
      };

    case 'DELETE_PDF':
      const { [action.pdfId]: _, ...rest } = state;
      return rest;

    default:
      return state;
  }
}

const PdfStateContext = createContext(null);

export function PdfStateProvider({ children }) {
  const [pdfStates, dispatch] = useReducer(pdfStateReducer, {});
  
  // Tiene traccia di quali PDF sono in polling
  const pollingRefs = useRef(new Map()); // Map<pdfId, intervalId>
  const isMountedRef = useRef(true);

  useEffect(() => {
    const intervalMap = pollingRefs.current;
    return () => {
      isMountedRef.current = false;
      // Cleanup di tutti i polling
      intervalMap.forEach(intervalId => clearInterval(intervalId));
      intervalMap.clear();
    };
  }, []);

  /**
   * Fetcha lo stato attuale di un PDF dal backend.
   * Questa è l'UNICA fonte di verità - tutti gli altri componenti
   * usano il context, non fanno fetch direttamente.
   */
  const fetchPdfState = useCallback(async (pdfId) => {
    try {
      const response = await api.get(`/pdfs/${pdfId}`);
      if (isMountedRef.current) {
        dispatch({
          type: 'SET_PDF',
          pdfId,
          data: response.data,
        });
      }
      return response.data;
    } catch (err) {
      console.warn(`[PdfStateContext] Fetch fallito per ${pdfId}:`, err.message);
      return null;
    }
  }, []);

  /**
   * Avvia il polling PER UN SINGOLO PDF.
   *
   * Questa funzione garantisce che:
   * - Un PDF non viene pollato 2+ volte contemporaneamente
   * - Se già in polling, la richiesta di polling è ignorata (idempotente)
   * - Il polling si ferma automaticamente quando lo stato è "ready" o "failed"
   */
  const startPollingPdf = useCallback((pdfId, seedData = null) => {
    // Se già in polling, non fare nulla (idempotent)
    if (pollingRefs.current.has(pdfId)) {
      return;
    }
    // Se abbiamo già uno stato in cache, usalo senza fare un fetch immediato
    const existing = pdfStates[pdfId];

    const maybeStartInterval = (data) => {
      if (!isMountedRef.current) return;
      const status = data?.processing_status;
      if (status === 'ready' || status === 'failed') return;

      const interval = setInterval(async () => {
        const currentData = await fetchPdfState(pdfId);
        if (!currentData) return;

        const currentStatus = currentData.processing_status;
        if (currentStatus === 'ready' || currentStatus === 'failed') {
          clearInterval(interval);
          pollingRefs.current.delete(pdfId);
        }
      }, POLL_INTERVAL_MS);

      pollingRefs.current.set(pdfId, interval);
    };

    if (existing) {
      maybeStartInterval(existing);
    } else if (seedData) {
      // Usa i dati forniti dal chiamante per evitare un fetch duplicato
      maybeStartInterval(seedData);
    } else {
      fetchPdfState(pdfId).then((data) => maybeStartInterval(data));
    }
  }, [fetchPdfState, pdfStates]);

  /**
   * Ferma il polling per un PDF specifico.
   */
  const stopPollingPdf = useCallback((pdfId) => {
    const intervalId = pollingRefs.current.get(pdfId);
    if (intervalId) {
      clearInterval(intervalId);
      pollingRefs.current.delete(pdfId);
    }
  }, []);

  /**
   * Ottiene lo stato attuale di un PDF dalla cache.
   * IMPORTANTE: questa è una lettura della cache, non fa fetch.
   * Se il PDF non è in cache, ritorna null.
   */
  const getPdfState = useCallback((pdfId) => {
    return pdfStates[pdfId] || null;
  }, [pdfStates]);

  return (
    <PdfStateContext.Provider
      value={{
        pdfStates,
        getPdfState,
        fetchPdfState,
        startPollingPdf,
        stopPollingPdf,
      }}
    >
      {children}
    </PdfStateContext.Provider>
  );
}

/**
 * Hook per accedere al PdfStateContext.
 */
export function usePdfState() {
  const ctx = useContext(PdfStateContext);
  if (!ctx) {
    throw new Error('usePdfState deve essere usato dentro un <PdfStateProvider>');
  }
  return ctx;
}
