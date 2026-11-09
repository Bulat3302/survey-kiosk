(function () {
  "use strict";

  // Запасная ссылка на опрос (используется, если сервер недоступен)
  var fallbackUrl = window.SURVEY_URL || "";
  if (fallbackUrl && !/^https?:\/\//i.test(fallbackUrl) && fallbackUrl.indexOf("//") !== 0) {
    fallbackUrl = new URL(fallbackUrl, window.location.href).href;
  }

  // Режим «только QR» (/?qr=1): левая панель скрыта, QR на весь экран
  if (window.location.search.indexOf("qr=1") !== -1) {
    document.querySelector(".kiosk").classList.add("qr-only");
  }

  var urlLabel = document.getElementById("survey-url");

  function renderQr(url) {
    if (!url) {
      return;
    }
    urlLabel.textContent = url;
    urlLabel.title = url;
    document.getElementById("qrcode").innerHTML = "";
    new QRCode(document.getElementById("qrcode"), {
      text: url,
      width: 320,
      height: 320,
      colorDark: "#0f172a",
      colorLight: "#ffffff",
      correctLevel: QRCode.CorrectLevel.M
    });
  }

  // Актуальную ссылку берём с сервера — её меняют на странице /setting/.
  // Если сервер недоступен, используем запасную из js/config.js.
  fetch("api/form-link")
    .then(function (r) {
      return r.json();
    })
    .then(function (data) {
      renderQr(data && data.form_url ? data.form_url : fallbackUrl);
    })
    .catch(function () {
      renderQr(fallbackUrl);
    });

  // Строка-статус под опросом: видно, доходят ли ответы
  var statusEl = document.getElementById("status");

  function updateStatus(data) {
    if (!statusEl) {
      return;
    }
    if (data && data.ok) {
      var t = data.ts ? new Date(data.ts).toLocaleTimeString("ru-RU") : "—";
      statusEl.textContent = "Ответов получено: " + data.count + " · последний: " + t;
    } else {
      statusEl.textContent = "Нет связи с сервером";
    }
  }

  function pollOnce() {
    fetch("api/last")
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        updateStatus(data);
        setTimeout(pollOnce, 5000);
      })
      .catch(function () {
        if (statusEl) {
          statusEl.textContent = "Нет связи с сервером";
        }
        setTimeout(pollOnce, 10000);
      });
  }

  pollOnce();
})();
