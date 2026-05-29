import React, { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { FileText, Download } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function SharedView() {
  const { token } = useParams();
  const navigate = useNavigate();
  const { user, loading } = useAuth();
  const [lib, setLib] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (loading) return undefined;
    if (!user) {
      navigate("/login", { replace: true, state: { from: `/shared/${token}` } });
      return undefined;
    }

    const ctrl = new AbortController();
    let alive = true;
    setLib(null);
    setError(null);

    api.get(`/shared/${token}`, { signal: ctrl.signal }).then((r) => {
      if (alive) setLib(r.data);
    }).catch((e) => {
      if (!alive || e.name === "CanceledError" || e.name === "AbortError" || e.code === "ERR_CANCELED") return;
      setError(e.response?.data?.detail || "Errore");
    });

    return () => {
      alive = false;
      ctrl.abort();
    };
  }, [token, user, loading, navigate]);

  const importPdf = async (pdfId) => {
    try { await api.post(`/pdfs/${pdfId}/import`); toast.success("PDF importato nella tua libreria"); }
    catch (e) { toast.error(e.response?.data?.detail || "Errore"); }
  };

  if (error) return (
    <div className="max-w-2xl mx-auto p-12 text-center" data-testid="shared-error">
      <h2 className="font-display text-2xl font-bold mb-2">Link non disponibile</h2>
      <p className="text-muted2">{error}</p>
    </div>
  );
  if (!lib) return <div className="p-12 text-mono text-sm text-muted2">Caricamento…</div>;

  return (
    <div className="max-w-6xl mx-auto px-6 md:px-12 py-12" data-testid="shared-view-page">
      <p className="overline mb-2">CONDIVISA CON TE</p>
      <h1 className="font-display font-black text-4xl tracking-tighter mb-1">{lib.name}</h1>
      {lib.description && <p className="text-[#525252] mb-6 max-w-2xl">{lib.description}</p>}
      <div className="bg-canvas2 border border-rule rounded-md p-4 mb-8 text-sm text-[#525252]" data-testid="shared-info">
        Aggiunto come membro. Se il proprietario rimuove la condivisione, potrai aggiungere i PDF alla tua libreria personale prima che spariscano.
      </div>

      <ul className="border-t border-rule">
        {(lib.pdfs || []).length === 0 && <li className="py-12 text-center text-muted2 text-sm">Nessun PDF.</li>}
        {(lib.pdfs || []).map((p) => (
          <li key={p.id} className="flex items-center justify-between gap-3 py-3 border-b border-rule group hover:bg-canvas2 px-2 -mx-2 transition-colors">
            <button onClick={() => navigate(`/viewer/${p.id}`)} className="text-left flex-1 flex items-center gap-3 min-w-0">
              <FileText size={16} strokeWidth={1.5} />
              <div className="min-w-0">
                <div className="font-medium hover:underline truncate">{p.title}</div>
                <div className="text-mono text-xs text-muted2">{p.created_at?.slice(0, 10)} · {p.pages}pp</div>
              </div>
            </button>
            <button onClick={() => importPdf(p.id)} className="btn-ghost border border-rule rounded-sm px-3 py-1.5 text-xs" data-testid={`import-pdf-${p.id}`}>
              <Download size={12} /> SALVA NELLA MIA LIBRERIA
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
