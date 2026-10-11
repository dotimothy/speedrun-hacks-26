import { StrictMode, lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";

const legacy = new URLSearchParams(location.search).get("view") === "pipeline";
const Screen = lazy(async () => {
  if (legacy) {
    await import("./prototype.css");
    return import("./PrototypeApp");
  }
  await import("./styles.css");
  return import("./App");
});
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Suspense fallback={<p>Loading workspace…</p>}>
      <Screen />
    </Suspense>
  </StrictMode>,
);
