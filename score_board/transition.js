"use strict";

const video = document.getElementById("transition-video");
let revision = -1;
let playingTransitionId = 0;
let playbackSafetyTimer = null;

function clearPlaybackSafetyTimer() {
  if (playbackSafetyTimer !== null) {
    clearTimeout(playbackSafetyTimer);
    playbackSafetyTimer = null;
  }
}

function hideVideo() {
  clearPlaybackSafetyTimer();
  video.classList.remove("active");
  video.pause();
}

function schedulePlaybackSafetyTimeout() {
  clearPlaybackSafetyTimer();
  const mediaDurationMs =
    Number.isFinite(video.duration) && video.duration > 0
      ? video.duration * 1000
      : 1600;
  const timeoutMs = Math.max(3000, mediaDurationMs + 1000);
  playbackSafetyTimer = setTimeout(() => {
    console.error("transition video playback timed out");
    hideVideo();
  }, timeoutMs);
}

async function acknowledge(transitionId) {
  const response = await fetch("transition-ack", {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transitionId }),
  });
  if (!response.ok) {
    throw new Error(`transition acknowledgement failed: ${response.status}`);
  }
}

async function playTransition(transitionId) {
  playingTransitionId = transitionId;
  clearPlaybackSafetyTimer();
  video.pause();
  video.currentTime = 0;
  video.classList.add("active");
  try {
    await video.play();
    schedulePlaybackSafetyTimeout();
    await acknowledge(transitionId);
  } catch (error) {
    console.error(error);
    if (playingTransitionId === transitionId) {
      hideVideo();
    }
  }
}

video.addEventListener("ended", () => {
  hideVideo();
});

video.addEventListener("error", () => {
  console.error("transition video could not be loaded", video.error);
  hideVideo();
});

async function watchTransitions() {
  while (true) {
    try {
      const response = await fetch(
        `transition-state.json?after=${encodeURIComponent(revision)}`,
        { cache: "no-store" },
      );
      if (!response.ok) {
        throw new Error(`transition state request failed: ${response.status}`);
      }
      const state = await response.json();
      revision = Number(state.revision ?? revision);
      const transitionId = Number(state.transitionId ?? 0);
      if (state.active && transitionId !== playingTransitionId) {
        await playTransition(transitionId);
      } else if (!state.active && transitionId === playingTransitionId) {
        // The controller's duration is a coordination timer, not proof that
        // the final video frame has rendered in OBS.  Let the media `ended`
        // event hide an actively playing clip so decoder/render delays cannot
        // cut off its ending.  Paused or completed media can still be cleaned
        // up immediately.
        if (video.ended || video.paused) {
          hideVideo();
        }
      }
    } catch (error) {
      console.error(error);
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
}

watchTransitions();
