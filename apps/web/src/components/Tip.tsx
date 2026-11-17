import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type FocusEvent,
  type MouseEvent,
  type ReactNode,
  type Ref,
} from "react";
import { createPortal } from "react-dom";

export interface TipTriggerProps {
  ref: Ref<HTMLButtonElement>;
  "aria-describedby": string;
  onMouseEnter: (e: MouseEvent) => void;
  onMouseLeave: (e: MouseEvent) => void;
  onFocus: (e: FocusEvent) => void;
  onBlur: (e: FocusEvent) => void;
  onClick: (e: MouseEvent) => void;
}

const GAP = 6;
const EDGE = 8;

/**
 * Tooltip for a button. It opens on hover and on keyboard focus, stays open while the pointer
 * is over it (WCAG 1.4.13), closes with Esc, and a click or tap pins it open (touch screens).
 *
 * The visible bubble is drawn in a portal, so a scrolling table cannot clip it. Screen readers
 * get the same text through a visually hidden element and `aria-describedby`.
 */
export function Tip({
  content,
  children,
}: {
  content: ReactNode;
  children: (trigger: TipTriggerProps) => ReactNode;
}) {
  const id = useId();
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const bubble = useRef<HTMLSpanElement | null>(null);
  const leaveTimer = useRef<number | undefined>(undefined);
  const open = (hover || focus || pinned) && !dismissed;

  const enter = useCallback(() => {
    window.clearTimeout(leaveTimer.current);
    setDismissed(false);
    setHover(true);
  }, []);
  const leave = useCallback(() => {
    // A short delay lets the pointer cross the gap between the button and the bubble.
    window.clearTimeout(leaveTimer.current);
    leaveTimer.current = window.setTimeout(() => setHover(false), 120);
  }, []);
  useEffect(() => () => window.clearTimeout(leaveTimer.current), []);

  useLayoutEffect(() => {
    if (!open || !trigger.current || !bubble.current) return;
    const place = () => {
      const t = trigger.current?.getBoundingClientRect();
      const b = bubble.current?.getBoundingClientRect();
      if (!t || !b) return;
      const vw = document.documentElement.clientWidth;
      const vh = document.documentElement.clientHeight;
      const left = Math.max(EDGE, Math.min(t.left, vw - b.width - EDGE));
      const below = t.bottom + GAP;
      const top = below + b.height > vh - EDGE && t.top - GAP - b.height > EDGE ? t.top - GAP - b.height : below;
      setPos({ top, left });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  // Esc closes from anywhere while open; a click elsewhere unpins.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        setDismissed(true);
        setPinned(false);
      }
    };
    const onDown = (e: globalThis.MouseEvent) => {
      const n = e.target as Node;
      if (!trigger.current?.contains(n) && !bubble.current?.contains(n)) setPinned(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  const props: TipTriggerProps = {
    ref: trigger,
    "aria-describedby": id,
    onMouseEnter: enter,
    onMouseLeave: leave,
    onFocus: () => {
      setDismissed(false);
      setFocus(true);
    },
    onBlur: () => setFocus(false),
    onClick: () => {
      setDismissed(false);
      setPinned((p) => !p);
    },
  };

  return (
    <>
      {children(props)}
      <span id={id} className="visually-hidden">
        {content}
      </span>
      {open &&
        createPortal(
          <span
            ref={bubble}
            className="tip-bubble"
            aria-hidden="true"
            style={{ top: pos?.top ?? 0, left: pos?.left ?? 0, visibility: pos ? "visible" : "hidden" }}
            onMouseEnter={enter}
            onMouseLeave={leave}
          >
            {content}
          </span>,
          document.body,
        )}
    </>
  );
}
