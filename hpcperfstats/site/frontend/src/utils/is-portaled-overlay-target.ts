const PORTALED_OVERLAY_SELECTOR = [
  '[data-slot="select-content"]',
  '[data-slot="popover-content"]',
  '[data-slot="dropdown-menu-content"]',
  '[data-slot="dropdown-menu-sub-content"]',
].join(", ");

/**
 * True when a pointer event target belongs to a portaled overlay
 * (Select / Popover / DropdownMenu content) mounted under document.body.
 * Outside-dismiss handlers that only check a panel ref must treat these as
 * inside the open surface, since they live outside the React parent subtree.
 */
export function isPortaledOverlayTarget(target: Node | null): boolean {
  if (target == null) return false;
  const element =
    target instanceof Element ? target : target.parentElement;
  if (element == null) return false;
  return element.closest(PORTALED_OVERLAY_SELECTOR) != null;
}
