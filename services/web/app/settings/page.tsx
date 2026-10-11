import AlertEmailSettings from "@/components/AlertEmailSettings";
import AnomalySensitivitySettings from "@/components/AnomalySensitivitySettings";
import WeeklyDigestSettings from "@/components/WeeklyDigestSettings";

export default function SettingsPage() {
  return (
    <div className="grid max-w-3xl gap-4">
      <AlertEmailSettings />
      <AnomalySensitivitySettings />
      <WeeklyDigestSettings />
    </div>
  );
}
