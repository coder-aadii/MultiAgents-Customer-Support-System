import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./hooks/useAuth";
import { TranslationProvider } from "./hooks/useTranslation";
import { NotificationProvider } from "./context/NotificationContext";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <TranslationProvider>
        <AuthProvider>
          <NotificationProvider>
            <App />
          </NotificationProvider>
        </AuthProvider>
      </TranslationProvider>
    </BrowserRouter>
  </React.StrictMode>
);
