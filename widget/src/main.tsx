import { init } from "./mount";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || "/api/v1";
const customerToken = sessionStorage.getItem("mcss_customer_token") || undefined;

init({
  apiBaseUrl,
  position: "bottom-right",
  accentColor: "#2563eb",
  title: "MCSS Support",
  customerToken,
});
