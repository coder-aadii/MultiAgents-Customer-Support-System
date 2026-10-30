import ReactDOM from "react-dom/client";
import Widget from "./Widget";
import "./index.css";

const config = {
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL || "/api/v1",
  position: "bottom-right" as const,
  accentColor: "#2563eb",
  title: "MCSS Support",
};

const root = ReactDOM.createRoot(document.getElementById("widget-root")!);
root.render(<Widget config={config} />);