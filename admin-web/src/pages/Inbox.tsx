import { useTranslation } from "../hooks/useTranslation";

export default function Inbox() {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">{t("inbox.title")}</h1>
      </div>
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <p className="text-gray-500 text-center py-12">{t("inbox.noConversations")}</p>
      </div>
    </div>
  );
}