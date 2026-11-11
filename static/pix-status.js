const version = document.currentScript.dataset.pixVersion;
const statusUrl = new URL(window.location.href);
statusUrl.searchParams.set("pix_status", "1");
let editing = false;
document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("input", () => { editing = true; });
});
async function checkPayment() {
  try {
    if (!editing && !document.hidden) {
      const response = await fetch(statusUrl);
      if (response.ok && !response.redirected) {
        const status = await response.json();
        if (!editing && status.version !== undefined && String(status.version) !== version) {
          window.location.reload();
          return;
        }
      }
    }
  } catch {
    // A próxima consulta continua quando a conexão local voltar.
  }
  window.setTimeout(checkPayment, 5000);
}
window.setTimeout(checkPayment, 5000);
