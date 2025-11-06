import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
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

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <div ref={setNodeRef} style={style} {...attributes} {...listeners}>
      <ServiceCard {...props} />
    </div>
  );
}
