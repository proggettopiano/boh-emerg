import React, { useEffect, useState } from "react";
import { Share2, FileText, ExternalLink, Shield } from "lucide-react";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function Shared() {
  const { user } = useAuth();
  const [pdfs, setPdfs] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const r = await api.get("/pdfs");
        // Filtra solo quelli NON protetti (quindi pubblici/condivisibili)
        setPdfs(r.data.items.filter(p => !p.is_protected));
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  return (
    <div className="max-w-7xl mx-auto px-6 md:px-12 py-12">
      <div className="mb-10">
        <p className="overline mb-2 flex items-center gap-2"><Share2 size={12} /> CONDIVISE</p>
        <h1 className="font-display font-black text-4xl md:text-5xl tracking-tighter">Spartiti Pubblici</h1>
        <p className="text-muted2 mt-2">Documenti accessibili anche tramite link esterno.</p>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3].map(i => <div key={i} className="h-32 bg-canvas2 animate-pulse rounded-md"></div>)}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {pdfs.map((p) => (
            <div key={p.id} className="border border-rule rounded-md p-4 bg-white hover:border-ink transition-colors group">
              <div className="flex items-start justify-between mb-3">
                <div className="p-2 bg-canvas2 rounded-sm text-ink">
                  <FileText size={20} />
                </div>
                <div className="flex items-center gap-2">
                   <span className="text-[10px] font-bold px-2 py-0.5 bg-emerald-100 text-emerald-700 rounded-full uppercase">Pubblico</span>
                </div>
              </div>
              <h3 className="font-bold text-lg leading-tight mb-1 truncate" title={p.title}>{p.title}</h3>
              <p className="text-xs text-muted3 font-mono uppercase tracking-widest mb-4">{p.pages} pagine</p>
              
              <div className="flex items-center gap-2 pt-4 border-t border-rule">
                <a 
                  href={`/shared/${p.id}`} 
                  target="_blank" 
                  rel="noopener noreferrer"
                  className="flex-1 flex items-center justify-center gap-2 py-2 bg-canvas2 hover:bg-ink hover:text-white rounded-sm text-xs font-bold transition-all"
                >
                  <ExternalLink size={14} /> Link Pubblico
                </a>
              </div>
            </div>
          ))}
          {pdfs.length === 0 && (
            <div className="col-span-full py-20 text-center border border-dashed border-rule rounded-md">
              <Share2 size={32} className="mx-auto mb-4 text-muted3" strokeWidth={1} />
              <p className="text-muted2 italic">Nessuno spartito pubblico disponibile.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
