import { init } from "./mount";
import { getActiveIndustry } from "../demo/showcase";

const activeIndustry = getActiveIndustry();
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || "/api/v1";
const customerToken = sessionStorage.getItem("mcss_customer_token") || undefined;

let customerName: string | undefined;
let customerEmail: string | undefined;
try {
  const storedProfile = sessionStorage.getItem("mcss_customer_profile");
  if (storedProfile) {
    const parsed = JSON.parse(storedProfile);
    customerName = parsed.name || parsed.label;
    customerEmail = parsed.email;
  }
} catch (e) {}

init({
  apiBaseUrl,
  position: "bottom-right",
  accentColor: activeIndustry.accentColor,
  title: `${activeIndustry.name} Support`,
  customerToken,
  customerName,
  customerEmail,
  orgSlug: activeIndustry.slug,
  welcomeMessage: activeIndustry.welcomeMessage,
});
