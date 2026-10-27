/* PWA shell and honest connectivity hints. The canon stays server-side. */
'use strict';
(() => {
  const installStatus = document.getElementById('mobile-install-status');
  const connectionStatus = document.getElementById('mobile-connection-status');
  if (!installStatus && !connectionStatus) return;

  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isStandalone = window.matchMedia?.('(display-mode: standalone)').matches
    || navigator.standalone === true;

  if (installStatus) {
    if (isStandalone) {
      installStatus.textContent = 'Umbral está abierto desde tu pantalla de inicio.';
    } else if (isIOS) {
      installStatus.textContent = 'Para añadir Umbral: abre esta dirección en Safari, toca Compartir, elige “Añadir a pantalla de inicio” y, si aparece, activa “Abrir como app web”.';
    } else if ('serviceWorker' in navigator) {
      installStatus.textContent = 'Puedes añadir Umbral a la pantalla de inicio desde las opciones de tu navegador.';
    } else {
      installStatus.textContent = 'Abre Umbral desde el navegador de tu dispositivo.';
    }
  }

  let lastCheck = 0;
  async function checkServer() {
    if (!connectionStatus) return;
    lastCheck = Date.now();
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('/health', {
        method: 'GET',
        cache: 'no-store',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      });
      const health = response.ok ? await response.json() : null;
      if (response.ok && health?.status === 'ok') {
        connectionStatus.textContent = 'El servidor responde ahora. Este chequeo no confirma tu sesión ni que el archivo central esté disponible.';
      } else {
        connectionStatus.textContent = 'No se pudo confirmar el estado del servidor. La conexión puede haberse interrumpido.';
      }
    } catch (_) {
      connectionStatus.textContent = navigator.onLine === false
        ? 'Este dispositivo indica que no tiene conexión. Los cambios de la nube necesitan internet y no se guardan para enviarse después.'
        : 'No se pudo comprobar el servidor. Los cambios de la nube necesitan conexión y no se guardan para enviarse después.';
    } finally {
      window.clearTimeout(timeout);
    }
  }

  window.addEventListener('online', checkServer);
  window.addEventListener('offline', checkServer);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && Date.now() - lastCheck > 60000) checkServer();
  });
  checkServer();
})();
