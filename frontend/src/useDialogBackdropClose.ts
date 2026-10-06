import { useRef } from 'react';
import type { MouseEvent, PointerEvent } from 'react';

function outside(event: MouseEvent<HTMLDialogElement> | PointerEvent<HTMLDialogElement>) {
  if (event.target !== event.currentTarget) return false;
  const rect = event.currentTarget.getBoundingClientRect();
  return event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
}

export function useDialogBackdropClose(close: () => void) {
  const startedOutside = useRef(false);
  return {
    onPointerDownCapture: (event: PointerEvent<HTMLDialogElement>) => { startedOutside.current = event.button === 0 && outside(event); },
    onPointerCancel: () => { startedOutside.current = false; },
    onClick: (event: MouseEvent<HTMLDialogElement>) => {
      const dismiss = startedOutside.current && outside(event);
      startedOutside.current = false;
      if (dismiss) close();
    },
  };
}
