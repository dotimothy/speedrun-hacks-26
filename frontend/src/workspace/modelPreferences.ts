import type { ModelInfo } from "../types";

export type ModelPreferences = { engine: string; whisperSize: string };
const key = "dispatchiq.model-preferences";

export function readModelPreferences(models: ModelInfo): ModelPreferences {
  let saved: Partial<ModelPreferences> = {};
  try { saved = JSON.parse(localStorage.getItem(key) || "{}") || {}; } catch { /* Storage can be unavailable. */ }
  const allowed = models.decision_engines.map(item => typeof item === "string" ? { name: item } : item)
    .filter(item => item.ready !== false).map(item => item.name);
  return {
    engine: typeof saved.engine === "string" && allowed.includes(saved.engine) ? saved.engine : "",
    whisperSize: typeof saved.whisperSize === "string" && models.whisper_sizes.includes(saved.whisperSize)
      ? saved.whisperSize : models.whisper_default,
  };
}

export function saveModelPreferences(value: ModelPreferences) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Selection still works for this page. */ }
}
