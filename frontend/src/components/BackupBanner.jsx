import React from "react";
import { AlertTriangle } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

export default function BackupBanner() {
  const { user } = useAuth();
  if (!user || user.backup_enabled) return null;
  return (
    <div
      className="bg-highlight text-highlightFg px-4 py-2 text-sm font-medium flex justify-center items-center gap-2 border-b border-[#FDE047]"
      data-testid="backup-warning-banner"
    >
      <AlertTriangle size={15} strokeWidth={2} />
      <span>
        Backup <strong>OFF</strong>. I file sono salvati solo in locale sul server. Se il server viene perso, dovrai ricaricarli.
      </span>
    </div>
  );
}
