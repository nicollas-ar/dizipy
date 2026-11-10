const { pixVersion, editForm } = document.currentScript.dataset;
const statusUrl = new URL(window.location.href);
statusUrl.searchParams.set("pix_status", "1");

let editing = false;
document.getElementById(editForm)?.addEventListener("input", () => {
  editing = true;
});

async function checkPayment() {
  try {
    if (!editing && !document.hidden) {
      const response = await fetch(statusUrl);
      if (response.ok && !response.redirected) {
        const status = await response.json();
        if (!editing && Number.isInteger(status.version) && status.version !== Number(pixVersion)) {
          window.location.reload();
          return;
        }
      }
    }
  } catch {
    // A conexão local pode voltar antes da próxima consulta.
  }
  window.setTimeout(checkPayment, 5000);
}

window.setTimeout(checkPayment, 5000);
