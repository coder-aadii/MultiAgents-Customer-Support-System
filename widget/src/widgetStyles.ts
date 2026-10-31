export function getWidgetStyles(accentColor: string = "#2563eb", position: string = "bottom-right"): string {
  const isRight = position !== "bottom-left";

  return `
    :host {
      all: initial;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      font-size: 14px;
      line-height: 1.5;
      color: #1f2937;
      box-sizing: border-box;
    }

    *, *::before, *::after {
      box-sizing: inherit;
    }

    .mcss-launcher-btn {
      position: fixed;
      bottom: 20px;
      ${isRight ? "right: 20px;" : "left: 20px;"}
      z-index: 2147483647;
      width: 56px;
      height: 56px;
      border-radius: 28px;
      background-color: ${accentColor};
      color: #ffffff;
      border: none;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: transform 0.2s ease, box-shadow 0.2s ease;
      outline: none;
    }

    .mcss-launcher-btn:hover {
      transform: scale(1.05);
      box-shadow: 0 6px 16px rgba(0, 0, 0, 0.2);
    }

    .mcss-launcher-btn:focus-visible {
      outline: 3px solid #93c5fd;
      outline-offset: 2px;
    }

    .mcss-panel {
      position: fixed;
      bottom: 86px;
      ${isRight ? "right: 20px;" : "left: 20px;"}
      z-index: 2147483647;
      width: 360px;
      height: 520px;
      max-height: calc(100vh - 100px);
      background-color: #ffffff;
      border-radius: 12px;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
      border: 1px solid #e5e7eb;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      animation: mcss-fade-in 0.2s ease-out;
    }

    @keyframes mcss-fade-in {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }

    @media (max-width: 480px) {
      .mcss-panel {
        top: 0;
        bottom: 0;
        left: 0;
        right: 0;
        width: 100vw;
        height: 100vh;
        max-height: 100vh;
        border-radius: 0;
        border: none;
      }
    }

    .mcss-header {
      background-color: ${accentColor};
      color: #ffffff;
      padding: 12px 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }

    .mcss-header h2 {
      margin: 0;
      font-size: 16px;
      font-weight: 600;
    }

    .mcss-close-btn {
      background: transparent;
      border: none;
      color: #ffffff;
      cursor: pointer;
      padding: 4px;
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      opacity: 0.85;
      outline: none;
    }

    .mcss-close-btn:hover {
      opacity: 1;
      background: rgba(255, 255, 255, 0.15);
    }

    .mcss-close-btn:focus-visible {
      outline: 2px solid #ffffff;
    }

    .mcss-contact-bar {
      background-color: #f9fafb;
      border-bottom: 1px solid #e5e7eb;
      padding: 8px 12px;
      font-size: 12px;
    }

    .mcss-contact-toggle {
      background: none;
      border: none;
      color: ${accentColor};
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      padding: 0;
      text-decoration: underline;
    }

    .mcss-contact-form {
      margin-top: 8px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .mcss-contact-input {
      padding: 6px 8px;
      border: 1px solid #d1d5db;
      border-radius: 6px;
      font-size: 12px;
      outline: none;
    }

    .mcss-contact-input:focus {
      border-color: ${accentColor};
      box-shadow: 0 0 0 1px ${accentColor};
    }

    .mcss-contact-save-btn {
      padding: 4px 10px;
      background-color: ${accentColor};
      color: #ffffff;
      border: none;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      align-self: flex-end;
    }

    .mcss-messages-container {
      flex: 1;
      overflow-y: auto;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      background-color: #f9fafb;
    }

    .mcss-message {
      display: flex;
      flex-direction: column;
      max-width: 80%;
    }

    .mcss-message.visitor {
      align-self: flex-end;
      align-items: flex-end;
    }

    .mcss-message.staff, .mcss-message.assistant {
      align-self: flex-start;
      align-items: flex-start;
    }

    .mcss-message.system {
      align-self: center;
      text-align: center;
      font-size: 12px;
      color: #6b7280;
      font-style: italic;
      background: #e5e7eb;
      padding: 4px 12px;
      border-radius: 12px;
      max-width: 90%;
    }

    .mcss-bubble {
      padding: 10px 14px;
      font-size: 13.5px;
      word-break: break-word;
      white-space: pre-wrap;
    }

    .mcss-bubble a {
      color: inherit;
      text-decoration: underline;
    }

    .mcss-message.visitor .mcss-bubble {
      background-color: ${accentColor};
      color: #ffffff;
      border-radius: 16px 16px 2px 16px;
    }

    .mcss-message.staff .mcss-bubble {
      background-color: #e0e7ff;
      color: #1e1b4b;
      border-radius: 16px 16px 16px 2px;
      border: 1px solid #c7d2fe;
    }

    .mcss-message.assistant .mcss-bubble {
      background-color: #ffffff;
      color: #111827;
      border: 1px solid #e5e7eb;
      border-radius: 16px 16px 16px 2px;
    }

    .mcss-sender-label {
      font-size: 11px;
      font-weight: 600;
      margin-bottom: 2px;
      color: #4b5563;
    }

    .mcss-source-chips {
      margin-top: 6px;
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }

    .mcss-source-chip {
      font-size: 11px;
      background-color: #f3f4f6;
      color: #374151;
      padding: 2px 6px;
      border-radius: 4px;
      border: 1px solid #e5e7eb;
    }

    .mcss-time-label {
      font-size: 10px;
      color: #9ca3af;
      margin-top: 2px;
    }

    .mcss-footer {
      padding: 12px;
      background-color: #ffffff;
      border-top: 1px solid #e5e7eb;
      flex-shrink: 0;
    }

    .mcss-form {
      display: flex;
      gap: 8px;
      align-items: flex-end;
    }

    .mcss-input {
      flex: 1;
      border: 1px solid #d1d5db;
      border-radius: 20px;
      padding: 8px 14px;
      font-size: 13.5px;
      font-family: inherit;
      resize: none;
      max-height: 100px;
      outline: none;
      line-height: 1.4;
    }

    .mcss-input:focus {
      border-color: ${accentColor};
      box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.2);
    }

    .mcss-send-btn {
      background-color: ${accentColor};
      color: #ffffff;
      border: none;
      border-radius: 18px;
      padding: 8px 14px;
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
      transition: opacity 0.2s;
      outline: none;
      flex-shrink: 0;
    }

    .mcss-send-btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .mcss-send-btn:focus-visible {
      outline: 2px solid ${accentColor};
      outline-offset: 1px;
    }

    .mcss-closed-box {
      text-align: center;
      padding: 8px;
      font-size: 12px;
      color: #6b7280;
      background-color: #f3f4f6;
      border-radius: 8px;
    }
  `;
}

