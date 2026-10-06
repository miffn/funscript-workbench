// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useDialogBackdropClose } from './useDialogBackdropClose';

beforeEach(() => { vi.stubGlobal('PointerEvent', MouseEvent); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
function Harness({ close }: { close: () => void }) {
  return <dialog open aria-label="Editor" {...useDialogBackdropClose(close)}><button>Inside</button></dialog>;
}
function setup() {
  const close = vi.fn(); render(<Harness close={close} />);
  const dialog = screen.getByRole('dialog');
  vi.spyOn(dialog, 'getBoundingClientRect').mockReturnValue({ left: 100, right: 300, top: 100, bottom: 300 } as DOMRect);
  return { close, dialog };
}
it.each([[50, 200], [350, 200], [200, 50], [200, 350]])('closes on a click outside the actual dialog bounds (%s,%s)', (clientX, clientY) => {
  const { close, dialog } = setup();
  fireEvent.pointerDown(dialog, { clientX, clientY, button: 0 }); fireEvent.click(dialog, { clientX, clientY });
  expect(close).toHaveBeenCalledOnce();
});
it('keeps dialog padding and child controls open', () => {
  const { close, dialog } = setup();
  fireEvent.pointerDown(dialog, { clientX: 110, clientY: 110, button: 0 }); fireEvent.click(dialog, { clientX: 110, clientY: 110 });
  const child = screen.getByRole('button');
  fireEvent.pointerDown(child, { clientX: 50, clientY: 50, button: 0 }); fireEvent.click(child, { clientX: 50, clientY: 50 });
  expect(close).not.toHaveBeenCalled();
});
it('does not dismiss after dragging across the boundary in either direction', () => {
  const { close, dialog } = setup();
  fireEvent.pointerDown(dialog, { clientX: 150, clientY: 150, button: 0 }); fireEvent.click(dialog, { clientX: 50, clientY: 50 });
  fireEvent.pointerDown(dialog, { clientX: 50, clientY: 50, button: 0 }); fireEvent.click(dialog, { clientX: 150, clientY: 150 });
  expect(close).not.toHaveBeenCalled();
});
it('ignores cancelled gestures and secondary mouse buttons', () => {
  const { close, dialog } = setup();
  fireEvent.pointerDown(dialog, { clientX: 50, clientY: 50, button: 0 }); fireEvent.pointerCancel(dialog); fireEvent.click(dialog, { clientX: 50, clientY: 50 });
  fireEvent.pointerDown(dialog, { clientX: 50, clientY: 50, button: 2 }); fireEvent.click(dialog, { clientX: 50, clientY: 50 });
  expect(close).not.toHaveBeenCalled();
});
