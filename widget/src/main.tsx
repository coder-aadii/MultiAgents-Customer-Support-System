import { init } from "./mount";
import { getActiveIndustry } from "../demo/showcase";

const activeIndustry = getActiveIndustry();
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || "/api/v1";
const customerToken = sessionStorage.getItem("mcss_customer_token") || undefined;

init({
  apiBaseUrl,
  position: "bottom-right",
  accentColor: activeIndustry.accentColor,
  title: `${activeIndustry.name} Support`,
  customerToken,
});
