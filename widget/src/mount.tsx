import ReactDOM from "react-dom/client";
import Widget, { type WidgetConfig } from "./Widget";
import { getWidgetStyles } from "./widgetStyles";

export interface WidgetInitOptions {
  apiBaseUrl: string;
  position?: "bottom-right" | "bottom-left";
  accentColor?: string;
  title?: string;
}

export interface MCSSWidgetInstance {
  destroy: () => void;
  root: ReactDOM.Root;
}

let activeInstance: MCSSWidgetInstance | null = null;
let activeHost: HTMLElement | null = null;

export function init(options: WidgetInitOptions): MCSSWidgetInstance {
  if (!options || typeof options !== "object") {
    throw new Error("MCSSWidget.init requires an options object");
  }

  if (!options.apiBaseUrl || typeof options.apiBaseUrl !== "string") {
    throw new Error("MCSSWidget.init: apiBaseUrl is required");
  }

  const normalizedOptions: WidgetConfig = {
    apiBaseUrl: options.apiBaseUrl.replace(/\/+$/, ""),
    position: options.position || "bottom-right",
    accentColor: options.accentColor || "#2563eb",
    title: options.title || "MCSS Support",
  };

  // Idempotent second-call behavior: exactly one launcher/root per target
  if (activeInstance && activeHost && document.body.contains(activeHost)) {
    activeInstance.root.render(<Widget config={normalizedOptions} />);
    return activeInstance;
  }

  // Remove any stale host if present
  const existingHost = document.getElementById("mcss-widget-host");
  if (existingHost && existingHost.parentNode) {
    existingHost.parentNode.removeChild(existingHost);
  }

  // Create isolated host element
  const host = document.createElement("div");
  host.id = "mcss-widget-host";
  host.style.cssText = "position: relative; z-index: 2147483647;";
  document.body.appendChild(host);
  activeHost = host;

  // Encapsulated Shadow DOM
  const shadowRoot = host.attachShadow({ mode: "open" });

  // Inject scoped styles inside Shadow DOM
  const styleEl = document.createElement("style");
  styleEl.textContent = getWidgetStyles(normalizedOptions.accentColor, normalizedOptions.position);
  shadowRoot.appendChild(styleEl);

  // Mount point inside Shadow DOM
  const container = document.createElement("div");
  container.id = "mcss-widget-mount";
  shadowRoot.appendChild(container);

  // Render React component
  const root = ReactDOM.createRoot(container);
  root.render(<Widget config={normalizedOptions} />);

  activeInstance = {
    destroy: () => {
      root.unmount();
      if (host.parentNode) {
        host.parentNode.removeChild(host);
      }
      activeInstance = null;
      activeHost = null;
    },
    root,
  };

  return activeInstance;
}

// Global exposure for standalone script integration
if (typeof window !== "undefined") {
  (window as any).MCSSWidget = {
    init,
  };
}

export default { init };

