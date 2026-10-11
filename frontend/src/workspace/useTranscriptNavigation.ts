import { useLayoutEffect, useRef, useState, type RefObject } from "react";

export function useTranscriptNavigation(
  pane: RefObject<HTMLDivElement>,
  sessionId: string,
  count: number,
  selected: number | null,
  view: string,
  translation: boolean,
) {
  const [following, setFollowing] = useState(true);
  const [seen, setSeen] = useState(count);
  const position = useRef(0);
  const previousView = useRef(view);
  const previousSource = useRef<number | null>(null);
  useLayoutEffect(() => {
    position.current = 0;
    setFollowing(true);
    setSeen(count);
  }, [sessionId]);
  useLayoutEffect(() => {
    if (selected !== null) setFollowing(false);
    const element = pane.current;
    const returning = previousView.current !== view;
    const sourceChanged = previousSource.current !== selected;
    previousSource.current = selected;
    previousView.current = view;
    if (!element) return;
    if (selected !== null && (sourceChanged || returning)) {
      const source = element.querySelector<HTMLElement>(
        `[data-segment="${selected}"]`,
      );
      if (source) {
        element.scrollTop +=
          source.getBoundingClientRect().top -
          element.getBoundingClientRect().top -
          12;
        position.current = element.scrollTop;
      }
    } else if (following && selected === null) {
      element.scrollTop = element.scrollHeight;
      position.current = element.scrollTop;
      setSeen(count);
    } else if (returning) element.scrollTop = position.current;
  }, [pane, count, selected, following, view, translation]);
  function onScroll() {
    const element = pane.current;
    if (!element) return;
    position.current = element.scrollTop;
    if (selected !== null) return;
    const atEnd =
      element.scrollHeight - element.scrollTop - element.clientHeight < 32;
    setFollowing(atEnd);
    if (atEnd) setSeen(count);
  }
  function followLatest() {
    setFollowing(true);
    setSeen(count);
    const element = pane.current;
    if (element) {
      element.scrollTop = element.scrollHeight;
      position.current = element.scrollTop;
    }
  }
  return {
    following: following && selected === null,
    unread: Math.max(0, count - seen),
    onScroll,
    followLatest,
  };
}
