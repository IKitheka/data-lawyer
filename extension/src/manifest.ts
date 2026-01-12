import type { ManifestV3Export } from "@crxjs/vite-plugin";

const manifest: ManifestV3Export = {
  manifest_version: 3,
  name: "Data Lawyer",
  version: "1.0.0",
  description: "Analyzes privacy policies and shows risk in your browser.",

  action: {
    default_popup: "index.html",
    default_title: "Data Lawyer",
  },

  // Minimal permissions for faster approval
  permissions: ["storage", "activeTab"],
  host_permissions: [],

  background: {
    service_worker: "src/background.ts",
    type: "module",
  },

  content_scripts: [
    {
      // Only inject where it matters (faster review)
      matches: ["*://*/*privacy*", "*://*/*terms*", "*://*/*policy*"],
      js: ["src/content-scripts/detectPolicyPages.ts"],
      run_at: "document_idle",
    },
  ],
};

export default manifest;
