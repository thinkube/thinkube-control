import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical } from 'lucide-react';
import { ServiceCard } from './ServiceCard';
import type { Service } from '@/stores/useServicesStore';

interface SortableServiceCardProps {
  service: Service;
  variant: 'full' | 'favorite';
  compact?: boolean;
  onToggleFavorite: (service: Service) => void;
  onShowDetails: (service: Service) => void;
  onRestart?: (service: Service) => void;
  onToggleService?: (service: Service, enabled: boolean) => void;
  onHealthCheck?: (service: Service) => void;
}

export function SortableServiceCard(props: SortableServiceCardProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: props.service.id });

  const style = { /* @allowed-inline - required by @dnd-kit/sortable for drag-and-drop positioning */
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <div
      ref={setNodeRef}
      style={style} /* @allowed-inline - @dnd-kit requires inline styles for transforms */
      className={`relative ${isDragging ? 'opacity-50' : ''}`}
    >
      {/* Drag Handle */}
      <div
        {...attributes}
        {...listeners}
        className="absolute top-2 left-2 z-10 cursor-grab active:cursor-grabbing p-1 rounded hover:bg-accent/20 transition-colors" /* @allowed-inline - drag handle styling required for UX */
        style={{ touchAction: 'none' }} /* @allowed-inline - required by @dnd-kit to prevent scrolling during drag */
      >
        <GripVertical className="h-4 w-4 text-muted-foreground" />
      </div>
      <ServiceCard {...props} />
    </div>
  );
}
