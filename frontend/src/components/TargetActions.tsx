import { Copy, Pause, Pencil, Play, Trash2 } from "lucide-react";
import { ActionMenu } from "./Popover";

export function TargetActions({ name, enabled, onToggle, onEdit, onClone, onDelete }: { name: string; enabled: boolean; onToggle: () => void; onEdit: () => void; onClone: () => void; onDelete: () => void }) {
  return <ActionMenu label={`Actions for ${name}`} actions={[
    { label: enabled ? "Pause" : "Resume", icon: enabled ? <Pause size={15} /> : <Play size={15} />, onClick: onToggle },
    { label: "Edit", icon: <Pencil size={15} />, onClick: onEdit },
    { label: "Clone", icon: <Copy size={15} />, onClick: onClone },
    { label: "Delete", icon: <Trash2 size={15} />, onClick: onDelete, danger: true },
  ]} />;
}
