(() => {
  "use strict";

  const themeRoot = "themes/aya-new/";
  const elements = {};
  let updateInFlight = false;
  let lastRenderedState = "";

  function byId(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    const element = elements[id] || byId(id);
    elements[id] = element;
    element.textContent = value == null ? "" : String(value);
  }

  function fitText(id, maximum, minimum = 11) {
    const element = elements[id] || byId(id);
    elements[id] = element;
    let size = maximum;
    element.style.fontSize = `${size}px`;
    while (size > minimum && element.scrollWidth > element.clientWidth) {
      size -= 1;
      element.style.fontSize = `${size}px`;
    }
  }

  function scoreImage(side, bestOf, score) {
    const numericScore = Number.parseInt(score, 10) || 0;
    if (numericScore <= 0 || !["bo3", "bo5"].includes(bestOf)) {
      return "";
    }
    return `${themeRoot}Aya_${side}p_${bestOf}_score${numericScore}.png`;
  }

  function setImageSource(id, source) {
    const element = elements[id] || byId(id);
    elements[id] = element;
    element.onerror = () => {
      element.style.display = "none";
      element.removeAttribute("src");
    };
    if (!source) {
      element.style.display = "none";
      element.removeAttribute("src");
      return;
    }
    element.style.display = "block";
    if (element.getAttribute("src") !== source) {
      element.src = source;
    }
  }

  function render(payload, state) {
    const isAya = state.design === "aya";
    byId("legacy-scoreboard").style.display = isAya ? "none" : "block";
    byId("aya-scoreboard").style.display = isAya ? "block" : "none";
    if (!isAya) return;

    const camera = Boolean(state.playerCamera);
    const between = camera && state.scene === "between_game";
    const framework = between
      ? "Aya_between_game.png"
      : camera
        ? "Aya_in_game_with_player_camera.png"
        : "Aya_in_game_with_no_player_camera.png";
    const bestOf = payload.bestOf === "bo5" ? "bo5" : "bo3";

    setImageSource("aya-framework", `${themeRoot}${framework}`);
    setImageSource("aya-best-of", `${themeRoot}Aya_${bestOf}.png`);
    setImageSource("aya-score-left", scoreImage(1, bestOf, payload.pScore1));
    setImageSource("aya-score-right", scoreImage(2, bestOf, payload.pScore2));

    setText("aya-stage", `${payload.stage || ""} [${bestOf.toUpperCase()}]`);
    setText("aya-player-left", payload.pName1 || "");
    setText("aya-player-right", payload.pName2 || "");
    setText("aya-prefix-left", payload.pTeam1 || "");
    setText("aya-prefix-right", payload.pTeam2 || "");
    setText("aya-rep-left", payload.pRep1 || "");
    setText("aya-rep-right", payload.pRep2 || "");
    fitText("aya-stage", 20, 12);
    fitText("aya-player-left", 24, 13);
    fitText("aya-player-right", 24, 13);
    fitText("aya-prefix-left", 15, 10);
    fitText("aya-prefix-right", 15, 10);
    fitText("aya-rep-left", 14, 10);
    fitText("aya-rep-right", 14, 10);
  }

  async function update() {
    if (updateInFlight) return;
    updateInFlight = true;
    try {
      const nonce = Date.now();
      const [payloadResponse, stateResponse] = await Promise.all([
        fetch(`/streamcontrol.json?t=${nonce}`, { cache: "no-store" }),
        fetch(`/scoreboard-state.json?t=${nonce}`, { cache: "no-store" }),
      ]);
      if (!payloadResponse.ok || !stateResponse.ok) return;
      const payload = await payloadResponse.json();
      const state = await stateResponse.json();
      const renderedState = JSON.stringify([payload, state]);
      if (renderedState !== lastRenderedState) {
        render(payload, state);
        lastRenderedState = renderedState;
      }
    } catch (_error) {
      // OBS may briefly reload the local server; the next poll will recover.
    } finally {
      updateInFlight = false;
    }
  }

  window.addEventListener("DOMContentLoaded", () => {
    update();
    window.setInterval(update, 400);
  });
})();
