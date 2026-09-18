import { Bell, BellOff, Copy, Pause, Pencil, Play, Trash2 } from "lucide-react";
import { ActionMenu } from "./Popover";

export function TargetActions({ name, enabled, notify = true, onToggle, onToggleNotify, onEdit, onClone, onDelete }: { name: string; enabled: boolean; notify?: boolean; onToggle: () => void; onToggleNotify?: () => void; onEdit: () => void; onClone: () => void; onDelete: () => void }) {
  return <ActionMenu label={`Actions for ${name}`} actions={[
    { label: enabled ? "Pause" : "Resume", icon: enabled ? <Pause size={15} /> : <Play size={15} />, onClick: onToggle },
    { label: "Edit", icon: <Pencil size={15} />, onClick: onEdit },
    { label: "Clone", icon: <Copy size={15} />, onClick: onClone },
    ...(onToggleNotify ? [{ label: notify ? "Mute notifications" : "Unmute notifications", icon: notify ? <BellOff size={15} /> : <Bell size={15} />, onClick: onToggleNotify }] : []),
    { label: "Delete", icon: <Trash2 size={15} />, onClick: onDelete, danger: true },
  ]} />;
}

/** Small marker for a target whose events are not delivered to the notification channels. */
export function MutedBadge({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-[11px] font-medium text-muted ${className}`} title="Notifications are muted for this target: events are recorded but not delivered to any channel">
      <BellOff size={11} /> Muted
    </span>
  );
}
