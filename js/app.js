(function () {
  "use strict";

  // Настройка из js/config.js
  var surveyUrl = window.SURVEY_URL || "";

  // Относительный путь дополняем адресом стойки
  if (surveyUrl && !/^https?:\/\//i.test(surveyUrl) && surveyUrl.indexOf("//") !== 0) {
    surveyUrl = new URL(surveyUrl, window.location.href).href;
  }

  // Режим «только QR» (/?qr=1): левая панель скрыта, QR на весь экран
  if (window.location.search.indexOf("qr=1") !== -1) {
    document.querySelector(".kiosk").classList.add("qr-only");
  }

  // Подпись с адресом под QR-кодом
  var urlLabel = document.getElementById("survey-url");
  urlLabel.textContent = surveyUrl;
  urlLabel.title = surveyUrl;

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

  // Правая половина: QR-код на официальный опрос
  new QRCode(document.getElementById("qrcode"), {
    text: surveyUrl,
    width: 320,
    height: 320,
    colorDark: "#0f172a",
    colorLight: "#ffffff",
    correctLevel: QRCode.CorrectLevel.M
  });
})();
