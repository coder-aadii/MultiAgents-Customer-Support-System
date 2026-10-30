import { useTranslation } from "../hooks/useTranslation";

export default function Knowledge() {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">{t("knowledge.title")}</h1>
        <button className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 text-sm">
          {t("knowledge.addDocument")}
        </button>
      </div>
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <p className="text-gray-500 text-center py-12">{t("knowledge.noDocuments")}</p>
      </div>
    </div>
  );
}