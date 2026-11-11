export interface IndustryProfile {
  key: string;
  name: string;
  slug: string;
  badge: string;
  icon: string;
  description: string;
  accentColor: string;
  bgLight: string;
  entities: string[];
  prompts: string[];
  sampleUser: {
    id: string;
    label: string;
    email: string;
    location?: string;
    ial: number;
  };
}

export const INDUSTRIES: Record<string, IndustryProfile> = {
  olist: {
    key: "olist",
    name: "E-Commerce (Olist)",
    slug: "default",
    badge: "Retail & Orders",
    icon: "🛒",
    description: "Real Brazilian E-Commerce dataset (99,441 customers, 99,441 orders, 112,650 line items).",
    accentColor: "#2563eb",
    bgLight: "#eff6ff",
    entities: ["order (ORD-*)", "shipment (TRK-*)", "refund (REF-*)", "customer (CUST-*)"],
    prompts: [
      "What is the status of my order ORD-1001?",
      "Track delivery shipment TRK-9901",
      "What is your return and refund policy?",
      "I need to speak with a human support agent",
    ],
    sampleUser: {
      id: "cust_3ce436f1",
      label: "Lucia Santos",
      email: "cust_3ce436f1@olist-store.com",
      location: "Rio de Janeiro, RJ",
      ial: 2,
    },
  },
  cloudpulse: {
    key: "cloudpulse",
    name: "CloudPulse SaaS",
    slug: "cloudpulse",
    badge: "B2B Cloud Infrastructure",
    icon: "⚡",
    description: "Cloud Observability & Telemetry Infrastructure (Workspaces, Quotas, Subscriptions).",
    accentColor: "#059669",
    bgLight: "#ecfdf5",
    entities: ["workspace (WS-*)", "subscription (SUB-*)", "api_quota (QUOTA-*)"],
    prompts: [
      "What is my data retention period on workspace WS-771a8b?",
      "What subscription tier is workspace WS-992b1c on?",
      "Check our monthly ingestion API quota usage",
      "Regenerate API key for workspace WS-771a8b",
    ],
    sampleUser: {
      id: "user_99",
      label: "Alex Rivera",
      email: "alex.rivera@cloudpulse-client.io",
      location: "San Francisco, CA",
      ial: 2,
    },
  },
  apex_bank: {
    key: "apex_bank",
    name: "Apex Global Bank",
    slug: "apex-bank",
    badge: "Fintech & Banking",
    icon: "🏛️",
    description: "Commercial & Retail Banking (Checking, Wire Transfers, Card Disputes, Fraud Triage).",
    accentColor: "#0f172a",
    bgLight: "#f8fafc",
    entities: ["account (ACC-*)", "transaction (TXN-*)", "wire_transfer (WIRE-*)"],
    prompts: [
      "What is the available balance on account ACC-440192?",
      "What is the status of wire transfer WIRE-88129?",
      "Dispute fraudulent transaction TXN-55102",
      "Connect me to an urgent fraud specialist",
    ],
    sampleUser: {
      id: "cust_bank_401",
      label: "Elena Rostova",
      email: "elena.rostova@apexbank-corp.com",
      location: "New York, NY",
      ial: 2,
    },
  },
  medicare: {
    key: "medicare",
    name: "MediCare Telehealth",
    slug: "medicare",
    badge: "Healthcare & Clinic",
    icon: "🩺",
    description: "Clinical Telehealth Portal (Appointments, Prescriptions, Nurse Handoff, Medical Triage).",
    accentColor: "#e11d48",
    bgLight: "#fff1f2",
    entities: ["patient (PAT-*)", "appointment (APT-*)", "prescription (RX-*)"],
    prompts: [
      "When is my upcoming appointment APT-1092?",
      "Check the status of prescription refill RX-44912",
      "I have severe chest pains and shortness of breath (Emergency Handoff)",
      "Connect me with an on-call triage nurse",
    ],
    sampleUser: {
      id: "pat_8812",
      label: "Marcus Vance",
      email: "marcus.vance@medicare-patient.org",
      location: "Chicago, IL",
      ial: 2,
    },
  },
};

export function getActiveIndustryKey(): string {
  try {
    return localStorage.getItem("mcss_showcase_industry") || "olist";
  } catch {
    return "olist";
  }
}

export function setActiveIndustryKey(key: string): void {
  try {
    localStorage.setItem("mcss_showcase_industry", key);
  } catch {}
}

export function getActiveIndustry(): IndustryProfile {
  const key = getActiveIndustryKey();
  return INDUSTRIES[key] || INDUSTRIES.olist;
}

