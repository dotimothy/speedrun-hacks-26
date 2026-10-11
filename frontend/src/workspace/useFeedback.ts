import { useLayoutEffect, useRef, type RefObject } from "react";
import type { Session } from "./session";

/** Short, cancellable acknowledgements. State changes never wait for motion. */
export function useFeedback(
  root: RefObject<HTMLDivElement>,
  session: Session,
  view: string,
  source: string | null,
) {
  const previous = useRef({ session, view, source });
  const running = useRef(new Set<Animation>());
  useLayoutEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const cancel = () => {
      running.current.forEach((animation) => animation.cancel());
      running.current.clear();
    };
    const preferenceChanged = () => {
      if (media.matches) cancel();
    };
    const element = root.current;
    element?.addEventListener("keydown", cancel);
    media.addEventListener("change", preferenceChanged);
    return () => {
      cancel();
      element?.removeEventListener("keydown", cancel);
      media.removeEventListener("change", preferenceChanged);
    };
  }, [root]);

  useLayoutEffect(() => {
    const old = previous.current;
    previous.current = { session, view, source };
    const desk = root.current;
    if (
      !desk ||
      desk.dataset.input !== "pointer" ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
      old.session.id !== session.id
    )
      return;

    function play(element: Element | null, frames: Keyframe[], duration = 180) {
      if (!element?.animate) return;
      element.getAnimations().forEach((animation) => animation.cancel());
      const animation = element.animate(frames, {
        duration,
        easing: "cubic-bezier(0.23, 1, 0.32, 1)",
      });
      running.current.add(animation);
      const remove = () => running.current.delete(animation);
      animation.onfinish = remove;
      animation.oncancel = remove;
    }
    if (old.view !== view) {
      play(
        desk.querySelector(
          view === "operator" ? ".workspace-grid" : ".receiving-panel",
        ),
        [{ opacity: 0.65 }, { opacity: 1 }],
        140,
      );
    } else {
      for (const field of Object.keys(
        session.record,
      ) as (keyof Session["record"])[]) {
        if (old.session.record[field] !== session.record[field]) {
          play(
            desk.querySelector(`[data-record-field="${field}"] .field-value`),
            [
              { opacity: 0.5, transform: "translateY(3px)" },
              { opacity: 1, transform: "translateY(0)" },
            ],
          );
          play(
            desk.querySelector(
              `[data-record-field="${field}"] .accepted-label svg`,
            ),
            [
              { opacity: 0, transform: "scale(0.85)" },
              { opacity: 1, transform: "scale(1)" },
            ],
          );
        }
      }
    }
    if (source && old.source !== source) {
      play(
        document.getElementById(`source-${source}`),
        [
          { opacity: 0.45, transform: "translateY(-3px)" },
          { opacity: 1, transform: "translateY(0)" },
        ],
        160,
      );
    }
    if (session.published !== old.session.published && session.published) {
      play(desk.querySelector(".publication-feedback svg"), [
        { opacity: 0, transform: "scale(0.85)" },
        { opacity: 1, transform: "scale(1)" },
      ]);
    }
  }, [root, session, view, source]);
}
