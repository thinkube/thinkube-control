/*
 * Copyright Alejandro Martínez Corriá and the Thinkube contributors
 * SPDX-License-Identifier: Apache-2.0
 */

import type { ComponentProps } from 'react';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical } from 'lucide-react';
import { TkButton } from 'thinkube-style/components/buttons-badges';
import { TkTooltip } from 'thinkube-style/components/modals-overlays';
import { ServiceCard } from './ServiceCard';

type SortableServiceCardProps = Omit<ComponentProps<typeof ServiceCard>, 'dragHandle'>;

export function SortableServiceCard(props: SortableServiceCardProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: props.service.id });

  const style = { /* @allowed-inline - required by @dnd-kit/sortable for drag-and-drop positioning */
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const dragHandle = (
    <TkTooltip content="Drag to reorder">
      <TkButton
        ref={setActivatorNodeRef}
        intent="ghost"
        size="icon"
        className="h-8 w-8 cursor-grab active:cursor-grabbing"
        style={{ touchAction: 'none' }} /* @allowed-inline - required by @dnd-kit to prevent scrolling during drag */
        aria-label="Drag to reorder"
        {...attributes}
        {...listeners}
      >
        <GripVertical className="h-4 w-4" />
      </TkButton>
    </TkTooltip>
  );

  return (
    <div
      ref={setNodeRef}
      style={style} /* @allowed-inline - @dnd-kit requires inline styles for transforms */
      className={`h-full ${isDragging ? 'relative z-10 opacity-50' : ''}`}
    >
      <ServiceCard {...props} dragHandle={dragHandle} />
    </div>
  );
}
