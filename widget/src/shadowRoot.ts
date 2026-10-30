export function createShadowRoot(): ShadowRoot {
  const host = document.createElement("div");
  host.id = "mcss-widget-host";
  host.style.cssText = `
    position: fixed;
    bottom: 20px;
    right: 20px;
    z-index: 2147483647;
    width: 0;
    height: 0;
    pointer-events: none;
  `;
  document.body.appendChild(host);
  return host.attachShadow({ mode: "open" });
}