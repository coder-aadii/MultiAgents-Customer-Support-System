import { useTranslation } from "../hooks/useTranslation";

export default function Audit() {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">{t("audit.title")}</h1>
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <p className="text-gray-500 text-center py-12">Audit log - coming soon</p>
      </div>
    </div>
  );
}