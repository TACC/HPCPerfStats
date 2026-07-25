/**
 * True when a pointer event target belongs to a portaled overlay
 * (Select / Popover / DropdownMenu content) mounted under document.body.
 *
 * Custom outside-dismiss handlers that only check a panel ref must treat
 * these roots as inside the open surface — they are intentionally outside
 * the React parent subtree.
 */
const PORTALED_OVERLAY_SELECTOR = [
  '[data-slot="select-content"]',
  '[data-slot="popover-content"]',
  '[data-slot="dropdown-menu-content"]',
  '[data-slot="dropdown-menu-sub-content"]',
].join(", ");

export function isPortaledOverlayTarget(target: Node | null): boolean {
  if (target == null) return false;
  const element =
    target instanceof Element ? target : target.parentElement;
  if (element == null) return false;
  return element.closest(PORTALED_OVERLAY_SELECTOR) != null;
}
