const notice = document.querySelector('#cookie-notice');
if (notice) {
  try {
    notice.hidden = localStorage.getItem('dizipy-cookie-notice') === 'seen';
  } catch {
    notice.hidden = false;
  }
  notice.querySelector('button').addEventListener('click', () => {
    try {
      localStorage.setItem('dizipy-cookie-notice', 'seen');
    } catch {
      // Keep the dismissal for the current page if storage is unavailable.
    }
    notice.hidden = true;
  });
}
