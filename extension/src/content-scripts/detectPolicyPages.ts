

function isPolicyPage(url: string, text: string) {
  const u = url.toLowerCase();
  const t = text.toLowerCase();

  return (
    u.includes("privacy") ||
    u.includes("terms") ||
    u.includes("policy") ||
    t.includes("privacy policy") ||
    t.includes("terms of service")
  );
}

async function run() {
  const url = window.location.href;
  const text = document.body.innerText + "\nTEST_CHANGE".slice(0, 20000);

  if (!isPolicyPage(url, text)) return;

  chrome.runtime.sendMessage(
    { type: "ANALYZE_POLICY", url, text, title:document.title },
    (resp) => {
      if (chrome.runtime.lastError) {
        console.error("[Data Lawyer] runtime message error:", chrome.runtime.lastError.message);
        return;
      }
      if (!resp?.ok) {
        console.error("[Data Lawyer] Analyze failed:", resp?.error);
        return;
      }
      console.log("[Data Lawyer] Analysis saved via background:", resp.data);
    }
  );
}

run();
