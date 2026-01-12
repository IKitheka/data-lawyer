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

  permissions: ["storage", "tabs"],
  host_permissions: [],

  background: {
    service_worker: "src/background.ts",
    type: "module",
  },

  content_scripts: [
    {
      matches: ["<all_urls>"],
      js: ["src/content-scripts/detectPolicyPages.ts"],
      run_at: "document_idle",
    },
  ],
};

export default manifest;
