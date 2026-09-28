import { useEffect, useState } from "react";
import Markdown from "react-markdown";

const PAIR_MS = 42;
const WORD = /\s*\S+/y;

/** Where the text shown so far ends after two more words: composition, not a typewriter. */
export function nextReveal(text: string, shown: number): number {
  let end = shown;
  for (let i = 0; i < 2; i++) {
    WORD.lastIndex = end;
    if (!WORD.test(text)) return text.length;
    end = WORD.lastIndex;
  }
  return end;
}

function useWordPairs(text: string): number {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (shown >= text.length) return;
    const timer = window.setTimeout(() => setShown((s) => nextReveal(text, s)), PAIR_MS);
    return () => window.clearTimeout(timer);
  }, [text, shown]);
  return Math.min(shown, text.length);
}

// Answers keep the app's type: emphasis and lists only, never headings, links or raw HTML.
const ALLOWED = ["p", "strong", "em", "ul", "ol", "li", "code"];

export function Answer({ text, done, running }: { text: string; done: boolean; running: boolean }) {
  const shown = useWordPairs(text);
  const complete = done && shown >= text.length;

  if (!text) {
    return running ? <div className="text-sm text-ink-5">Writing the answer…</div> : null;
  }
  return (
    <>
      <div
        aria-hidden={!complete}
        className={`oq-answer max-w-[74ch] text-[15.5px] leading-[1.72] text-pretty text-ink ${complete ? "" : "oq-typing"}`}
      >
        <Markdown allowedElements={ALLOWED} unwrapDisallowed>
          {text.slice(0, shown)}
        </Markdown>
      </div>
      {/* Announced once, whole, rather than word by word. */}
      <div role="status" className="sr-only">
        {complete ? text : ""}
      </div>
    </>
  );
}
