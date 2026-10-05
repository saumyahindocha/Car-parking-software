/* RaRa's birthday site — all behaviour. Words live in js/content.js. */
(function () {
  "use strict";

  var C = window.CONTENT;
  var S = C.settings;
  var PHOTOS = window.PHOTOS || {};
  var Lily = window.Lily;

  /* -------------------------------------------------------------- */
  /* helpers                                                         */
  /* -------------------------------------------------------------- */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  // tiny markdown: **bold**, *italic*, \n → line break
  function md(s) {
    return esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(/\n/g, "<br>");
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function store(key, val) {
    try { if (val === undefined) return localStorage.getItem(key); localStorage.setItem(key, val); } catch (e) { return null; }
  }
  function sstore(key, val) {
    try { if (val === undefined) return sessionStorage.getItem(key); sessionStorage.setItem(key, val); } catch (e) { return null; }
  }
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* -------------------------------------------------------------- */
  /* time (always India time — the targets carry +05:30)             */
  /* -------------------------------------------------------------- */
  var params = new URLSearchParams(location.search);
  if (params.get("preview") === S.previewKey) sstore("rara-preview", "1");
  var isPreview = sstore("rara-preview") === "1";
  if (isPreview && params.has("reset")) { try { localStorage.removeItem("rara-unlocked"); } catch (e) {} }

  // Preview-only clock override for testing, e.g. ?preview=ash&now=2026-10-18T10:00
  var clockOffset = 0;
  if (isPreview && params.get("now")) {
    var fake = Date.parse(params.get("now").length <= 16 ? params.get("now") + ":00+05:30" : params.get("now"));
    if (!isNaN(fake)) clockOffset = fake - Date.now();
  }
  function now() { return Date.now() + clockOffset; }
  var BIRTHDAY = Date.parse(S.birthday);
  var NAVRATRI = Date.parse(S.navratri);

  function split(ms) {
    ms = Math.max(0, ms);
    var s = Math.floor(ms / 1000);
    return { days: Math.floor(s / 86400), hours: Math.floor(s / 3600) % 24, minutes: Math.floor(s / 60) % 60, seconds: s % 60 };
  }
  function countdownHTML(labels, cls) {
    return '<div class="clock ' + (cls || "") + '" role="timer">' +
      ["days", "hours", "minutes", "seconds"].map(function (k) {
        return '<div class="clock__cell"><span class="clock__num" data-k="' + k + '">00</span><span class="clock__label">' + esc(labels[k]) + "</span></div>";
      }).join("") + "</div>";
  }
  function paintClock(root, ms) {
    var t = split(ms);
    $$(".clock__num", root).forEach(function (el) {
      var v = el.getAttribute("data-k") === "days" ? String(t.days) : pad(t[el.getAttribute("data-k")]);
      if (el.textContent !== v) el.textContent = v;
    });
  }

  /* -------------------------------------------------------------- */
  /* drifting petals                                                 */
  /* -------------------------------------------------------------- */
  function startPetals(count) {
    if (reduceMotion) return;
    var layer = $("#petals");
    layer.innerHTML = "";
    for (var i = 0; i < count; i++) {
      var p = document.createElement("span");
      p.className = "petal-drift";
      p.innerHTML = Lily.petal();
      p.style.left = (5 + Math.random() * 90) + "%";
      p.style.setProperty("--dur", (16 + Math.random() * 12) + "s");
      p.style.setProperty("--delay", (-Math.random() * 24) + "s");
      p.style.setProperty("--sway", (20 + Math.random() * 40) + "px");
      p.style.setProperty("--size", (10 + Math.random() * 8) + "px");
      p.style.setProperty("--rot", (Math.random() * 360) + "deg");
      layer.appendChild(p);
    }
  }
  function burstPetals(n, originEl) {
    if (reduceMotion) return;
    var r = originEl ? originEl.getBoundingClientRect() : { left: 0, width: innerWidth, top: innerHeight * 0.3 };
    for (var i = 0; i < n; i++) {
      var p = document.createElement("span");
      p.className = "petal-fall";
      p.innerHTML = Lily.petal();
      p.style.left = (r.left + Math.random() * r.width) + "px";
      p.style.top = (r.top - 20 + Math.random() * 40) + "px";
      p.style.setProperty("--dx", (Math.random() * 160 - 80) + "px");
      p.style.setProperty("--dur", (3 + Math.random() * 2.5) + "s");
      p.style.setProperty("--delay", (Math.random() * 0.8) + "s");
      p.style.setProperty("--rot", (Math.random() * 720 - 360) + "deg");
      p.style.setProperty("--size", (12 + Math.random() * 10) + "px");
      document.body.appendChild(p);
      setTimeout(function (el) { el.remove(); }, 7000, p);
    }
  }

  /* -------------------------------------------------------------- */
  /* audio — iOS-safe                                                */
  /*  • first play happens inside the unlock tap                      */
  /*  • the other tracks are "primed" in that same tap so later       */
  /*    programmatic play() calls are allowed                         */
  /*  • fades use Web Audio gain ONLY when navigator.audioSession     */
  /*    exists (so the silent switch doesn't mute it); otherwise       */
  /*    volume ramps where supported, else a clean pause → play swap  */
  /* -------------------------------------------------------------- */
  var Music = (function () {
    var els = { main: $("#audio-main"), letter: $("#audio-letter") };
    var voice = $("#audio-voice");
    var gains = {}, tokens = { main: 0, letter: 0 };
    var ctx = null, useWA = false, unlocked = false, muted = false, desired = null;
    var canVolume = (function () { try { var a = document.createElement("audio"); a.volume = 0.5; return a.volume === 0.5; } catch (e) { return false; } })();
    var FADE_IN = 1.4, FADE_OUT = 0.9;

    function load() {
      if (!els.main.src) {
        els.main.src = C.audio.main.src;
        els.letter.src = C.audio.letter.src;
        voice.src = C.audio.voice.src;
      }
    }
    function safePlay(el) {
      try { var p = el.play(); if (p && p.catch) p.catch(function () {}); return p; } catch (e) { return null; }
    }
    function setLevel(key, v) {
      if (useWA && gains[key]) { gains[key].gain.cancelScheduledValues(ctx.currentTime); gains[key].gain.setValueAtTime(v, ctx.currentTime); }
      else if (canVolume) els[key].volume = v;
    }
    function ramp(key, to, secs, done) {
      var el = els[key];
      if (useWA && gains[key]) {
        var g = gains[key].gain, t = ctx.currentTime;
        g.cancelScheduledValues(t); g.setValueAtTime(g.value, t); g.linearRampToValueAtTime(to, t + secs);
        if (done) setTimeout(done, secs * 1000 + 30);
      } else if (canVolume) {
        var from = el.volume, start = performance.now();
        (function step(ts) {
          var k = Math.min(1, (ts - start) / (secs * 1000));
          el.volume = Math.max(0, Math.min(1, from + (to - from) * k));
          if (k < 1) requestAnimationFrame(step); else if (done) done();
        })(start);
      } else if (done) done(); // iOS without audioSession: instant
    }
    function startTrack(key) {
      var el = els[key], my = ++tokens[key];
      if (!el.paused && el.dataset.fadingOut !== "1") return;
      el.dataset.fadingOut = "0";
      if (useWA || canVolume) setLevel(key, el.paused ? 0 : (useWA ? gains[key].gain.value : el.volume));
      var p = safePlay(el);
      var go = function () { if (tokens[key] === my) ramp(key, 1, FADE_IN); };
      if (p && p.then) p.then(go, function () {}); else go();
    }
    function stopTrack(key, instant) {
      var el = els[key], my = ++tokens[key];
      if (el.paused) return;
      if (instant || !(useWA || canVolume)) { el.pause(); return; }
      el.dataset.fadingOut = "1";
      ramp(key, 0, FADE_OUT, function () { if (tokens[key] === my) { el.pause(); el.dataset.fadingOut = "0"; } });
    }
    function apply(fromUser) {
      if (!unlocked) return;
      var swap = !(useWA || canVolume); // iOS w/o audioSession: hard swap
      Object.keys(els).forEach(function (k) { if (k !== desired || muted) stopTrack(k, swap); });
      if (desired && !muted) {
        if (!voice.paused) voice.pause();
        startTrack(desired);
      }
      updateButton();
    }
    function prime(el) {
      // Play silently for a moment inside the user gesture so iOS lets us play it later.
      var wasMuted = el.muted;
      el.muted = true;
      var p = safePlay(el);
      var after = function () { el.pause(); try { el.currentTime = 0; } catch (e) {} el.muted = wasMuted; };
      if (p && p.then) p.then(after, function () { el.muted = wasMuted; }); else after();
    }
    function unlock() {
      // MUST be called synchronously inside a tap/click handler.
      load();
      if (unlocked) { apply(true); return; }
      unlocked = true;
      try {
        if (navigator.audioSession && window.AudioContext) {
          navigator.audioSession.type = "playback";
          ctx = new AudioContext();
          Object.keys(els).forEach(function (k) {
            var src = ctx.createMediaElementSource(els[k]);
            gains[k] = ctx.createGain();
            gains[k].gain.value = 0;
            src.connect(gains[k]).connect(ctx.destination);
          });
          ctx.resume();
          useWA = true;
        }
      } catch (e) { useWA = false; }
      if (!desired) desired = "main";
      $("#mute").hidden = false;
      apply(true);                         // starts the track synchronously, inside the gesture
      prime(els[desired === "main" ? "letter" : "main"]);
      prime(voice);
    }
    function want(key) {
      if (key === "none") key = null;
      if (key === desired) return;
      var prev = desired;
      desired = key;
      apply(false);
      if (key && prev !== undefined && unlocked && !muted) toast(C.audio[key].title);
    }
    function kick() {
      // Safety net on any tap: resume anything iOS paused on us.
      if (!unlocked) return;
      if (ctx && ctx.state !== "running") { try { ctx.resume(); } catch (e) {} }
      if (desired && !muted && els[desired].paused) startTrack(desired);
    }
    function toggleMute() {
      muted = !muted;
      if (!muted && desired) { if (ctx) ctx.resume(); safePlay(els[desired]); }
      apply(true);
    }
    function isPlaying() { return !!(desired && !muted); }
    function updateButton() {
      var b = $("#mute"), on = isPlaying() || !voice.paused;
      b.setAttribute("aria-pressed", muted ? "true" : "false");
      b.setAttribute("aria-label", muted ? C.ui.unmute : C.ui.mute);
      b.classList.toggle("is-muted", muted);
      b.classList.toggle("is-playing", on && !muted);
    }
    function playVoice() {
      Object.keys(els).forEach(function (k) { stopTrack(k, true); });
      load();
      if (ctx) ctx.resume();
      return safePlay(voice);
    }
    return { unlock: unlock, want: want, kick: kick, toggleMute: toggleMute, playVoice: playVoice, voice: voice, load: load, updateButton: updateButton,
      get desired() { return desired; } };
  })();

  var toastTimer;
  function toast(text) {
    var t = $("#toast");
    t.innerHTML = '<span class="toast__k">♪ ' + esc(C.ui.nowPlaying) + "</span>" + esc(text);
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 3200);
  }

  /* -------------------------------------------------------------- */
  /* 1. countdown                                                    */
  /* -------------------------------------------------------------- */
  function showCountdown() {
    var el = $("#countdown"), c = C.countdown;
    el.innerHTML =
      '<div class="screen__sky"></div>' +
      Lily.sprig({ className: "corner corner--tl" }) + Lily.sprig({ className: "corner corner--br" }) +
      '<div class="screen__inner">' +
        '<p class="eyebrow hand">' + esc(c.eyebrow) + "</p>" +
        '<div class="countdown__lily">' + Lily.flower({ bloom: true, className: "is-closed breathe" }) + "</div>" +
        '<h1 class="countdown__line">' + md(c.line) + "</h1>" +
        countdownHTML(c.labels) +
        '<p class="countdown__date">' + esc(c.dateLine) + "</p>" +
        '<p class="countdown__sub hand">' + esc(c.sub) + "</p>" +
      "</div>";
    el.hidden = false;
    startPetals(5);
    setTimeout(function () { $$(".lily--bloom", el).forEach(function (l) { l.classList.remove("is-closed"); }); }, 300);
    var tick = function () {
      var left = BIRTHDAY - now();
      paintClock(el, left);
      if (left <= 0) { clearInterval(iv); fadeOut(el, showGate); }
    };
    var iv = setInterval(tick, 1000);
    tick();
  }
  function fadeOut(el, next) {
    el.classList.add("is-leaving");
    setTimeout(function () { el.hidden = true; el.classList.remove("is-leaving"); if (next) next(); }, reduceMotion ? 10 : 900);
  }

  /* -------------------------------------------------------------- */
  /* 2. password gate                                                */
  /*    The answer is never stored — only a hash of the normalised    */
  /*    "DD-MM" form. Any common way of writing the date is accepted. */
  /* -------------------------------------------------------------- */
  var ANSWER = "f16kqlb2l0";
  var SALT = "lily|";
  function cyrb53(str, seed) {
    var h1 = 0xdeadbeef ^ (seed || 19), h2 = 0x41c6ce57 ^ (seed || 19);
    for (var i = 0, ch; i < str.length; i++) { ch = str.charCodeAt(i); h1 = Math.imul(h1 ^ ch, 2654435761); h2 = Math.imul(h2 ^ ch, 1597334677); }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507); h1 ^= Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507); h2 ^= Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
  }
  var MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
  function dateCandidates(raw) {
    var s = String(raw || "").toLowerCase().trim();
    s = s.replace(/(\d)\s*(st|nd|rd|th)\b/g, "$1").replace(/\b(of|the)\b/g, " ");
    var out = [];
    function add(d, m, y) {
      d = +d; m = +m;
      if (!(d >= 1 && d <= 31 && m >= 1 && m <= 12)) return;
      if (y !== undefined && y !== null && y !== "") { y = +y; if (y !== 26 && y !== 2026) return; }
      out.push(pad(d) + "-" + pad(m));
    }
    var monthIdx = 0;
    var mm = s.match(/[a-z]{3,}/g) || [];
    for (var i = 0; i < mm.length && !monthIdx; i++) {
      monthIdx = MONTHS.indexOf(mm[i].slice(0, 3)) + 1;
      if (!monthIdx && /^a+u?g/.test(mm[i])) monthIdx = 8; // forgive "agust", "agst"…
    }
    var nums = s.match(/\d+/g) || [];
    if (monthIdx) {
      var small = nums.filter(function (n) { return n.length <= 2; });
      var big = nums.filter(function (n) { return n.length === 4; });
      if (small.length === 1 && big.length <= 1) add(small[0], monthIdx, big[0]);
      else if (small.length === 2 && !big.length) { add(small[0], monthIdx, small[1]); add(small[1], monthIdx, small[0]); }
      return out;
    }
    if (nums.length === 1) {
      var n = nums[0];
      if (n.length === 4) { add(n.slice(0, 2), n.slice(2)); add(n.slice(2), n.slice(0, 2)); }
      else if (n.length === 3) { add(n.slice(0, 2), n.slice(2)); add(n.slice(1), n.slice(0, 1)); }
      else if (n.length === 6) { add(n.slice(0, 2), n.slice(2, 4), n.slice(4)); add(n.slice(2, 4), n.slice(0, 2), n.slice(4)); }
      else if (n.length === 8) {
        add(n.slice(0, 2), n.slice(2, 4), n.slice(4)); add(n.slice(2, 4), n.slice(0, 2), n.slice(4));
        add(n.slice(6), n.slice(4, 6), n.slice(0, 4));
      }
    } else if (nums.length === 2 || nums.length === 3) {
      if (nums[0].length === 4 && nums.length === 3) add(nums[2], nums[1], nums[0]);
      else { add(nums[0], nums[1], nums[2]); add(nums[1], nums[0], nums[2]); }
    }
    return out;
  }
  function checkAnswer(raw) {
    return dateCandidates(raw).some(function (c) { return cyrb53(SALT + c) === ANSWER; });
  }
  window.__rara = { dateCandidates: dateCandidates, checkAnswer: checkAnswer }; // handy for testing in the console

  function showGate() {
    var el = $("#gate"), g = C.gate;
    var returning = store("rara-unlocked") === "1";
    Music.load();
    el.innerHTML =
      '<div class="screen__sky"></div>' +
      Lily.sprig({ className: "corner corner--tl" }) + Lily.sprig({ className: "corner corner--br" }) +
      '<div class="screen__inner">' +
      (returning
        ? '<div class="gate__card">' +
            '<div class="gate__lily">' + Lily.flower({ bloom: true, className: "is-closed breathe" }) + "</div>" +
            '<h1 class="gate__title">' + esc(g.welcomeBack) + "</h1>" +
            '<button class="btn btn--primary" id="gate-again" type="button">' + esc(g.welcomeBackButton) + "</button>" +
          "</div>"
        : '<form class="gate__card" id="gate-form" autocomplete="off" novalidate>' +
            '<div class="gate__lily">' + Lily.flower({ bloom: true, className: "is-closed breathe" }) + "</div>" +
            '<p class="eyebrow">' + esc(g.eyebrow) + "</p>" +
            '<h1 class="gate__title">' + esc(g.title) + "</h1>" +
            '<label class="gate__prompt" for="gate-input">' + esc(g.prompt) + "</label>" +
            '<input id="gate-input" class="gate__input" type="text" inputmode="text" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" enterkeyhint="go" placeholder="' + esc(g.placeholder) + '">' +
            '<p class="gate__msg" id="gate-msg" role="status"></p>' +
            '<button class="btn btn--primary" type="submit">' + esc(g.button) + "</button>" +
            '<button class="gate__hintbtn" type="button" id="gate-hintbtn" aria-expanded="false">' + esc(g.hintButton) + "</button>" +
            '<p class="gate__hint hand" id="gate-hint" hidden>' + esc(g.hint) + "</p>" +
          "</form>") +
      "</div>";
    el.hidden = false;
    startPetals(6);
    requestAnimationFrame(function () { $$(".lily--bloom", el).forEach(function (l) { l.classList.remove("is-closed"); }); });

    if (returning) {
      $("#gate-again").addEventListener("click", function () { Music.unlock(); enterSite(); });
      return;
    }
    var form = $("#gate-form"), input = $("#gate-input"), msg = $("#gate-msg"), tries = 0;
    $("#gate-hintbtn").addEventListener("click", function () {
      var h = $("#gate-hint"); h.hidden = !h.hidden; this.setAttribute("aria-expanded", String(!h.hidden));
    });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (checkAnswer(input.value)) {
        Music.unlock();               // inside the tap → iOS allows audio
        store("rara-unlocked", "1");
        msg.textContent = g.success;
        msg.className = "gate__msg is-good";
        input.blur();
        enterSite();
      } else {
        msg.textContent = input.value.trim() ? g.wrong[tries++ % g.wrong.length] : g.prompt;
        msg.className = "gate__msg is-bad";
        form.classList.remove("shake"); void form.offsetWidth; form.classList.add("shake");
        if (tries >= 2) { $("#gate-hint").hidden = false; }
      }
    });
  }

  /* -------------------------------------------------------------- */
  /* unlock → bloom → site                                           */
  /* -------------------------------------------------------------- */
  var entered = false;
  function enterSite() {
    if (entered) return;
    entered = true;
    var bloom = $("#bloom");
    var spots = [[50, 46, 1.25], [20, 22, .6], [82, 26, .7], [16, 76, .7], [84, 74, .6], [50, 88, .5], [50, 10, .45]];
    bloom.innerHTML = spots.map(function (s, i) {
      return '<div class="bloom__lily" style="left:' + s[0] + "%;top:" + s[1] + "%;--s:" + s[2] + ";--d:" + (i * 110) + 'ms">' + Lily.flower({ bloom: true, className: "is-closed" }) + "</div>";
    }).join("");
    bloom.classList.add("show");
    requestAnimationFrame(function () { requestAnimationFrame(function () { $$(".lily", bloom).forEach(function (l) { l.classList.remove("is-closed"); }); }); });
    burstPetals(18, document.body);

    setTimeout(function () {
      $("#gate").hidden = true;
      $("#countdown").hidden = true;
      document.body.classList.remove("is-locked");
      $("#site").hidden = false;
      window.scrollTo(0, 0);
      startPetals(7);
      initReveals();
      initSongZones();
      bloom.classList.add("fade");
      setTimeout(function () { bloom.classList.remove("show", "fade"); bloom.innerHTML = ""; }, 1400);
      // If iOS refused the first play (rare), ask for one more tap.
      setTimeout(function () {
        if (Music.desired && $("#audio-" + Music.desired).paused && !$("#mute").classList.contains("is-muted")) showTapToBegin();
      }, 1800);
    }, reduceMotion ? 300 : 2100);
  }
  function showTapToBegin() {
    var t = document.createElement("button");
    t.className = "tapbegin"; t.type = "button";
    t.textContent = C.gate.tapToBegin;
    document.body.appendChild(t);
    var done = false;
    var go = function () {
      if (done) return; done = true;
      Music.unlock(); t.classList.add("bye"); setTimeout(function () { t.remove(); }, 600);
    };
    document.addEventListener("click", go, { once: true, capture: true });
  }

  /* -------------------------------------------------------------- */
  /* scroll reveals + which song belongs where                       */
  /* -------------------------------------------------------------- */
  var revealIO;
  function initReveals() {
    if (!("IntersectionObserver" in window) || reduceMotion) {
      $$(".reveal").forEach(function (el) { el.classList.add("in"); });
      $$(".lily--bloom.is-closed").forEach(function (el) { el.classList.remove("is-closed"); });
      return;
    }
    revealIO = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.classList.add("in");
          $$(".lily--bloom.is-closed", en.target).forEach(function (l) { l.classList.remove("is-closed"); });
          revealIO.unobserve(en.target);
        }
      });
    }, { rootMargin: "0px 0px -12% 0px", threshold: 0.12 });
    $$(".reveal").forEach(function (el) { revealIO.observe(el); });
  }
  function initSongZones() {
    var sections = $$("#site > section[data-song]");
    var pick = function () {
      var mid = innerHeight * 0.55, chosen = null;
      for (var i = 0; i < sections.length; i++) {
        var r = sections[i].getBoundingClientRect();
        if (r.top <= mid) chosen = sections[i];
      }
      if (chosen) Music.want(chosen.getAttribute("data-song"));
    };
    var raf = 0;
    window.addEventListener("scroll", function () { if (!raf) raf = requestAnimationFrame(function () { raf = 0; pick(); }); }, { passive: true });
    pick();
  }

  /* -------------------------------------------------------------- */
  /* section builders                                                */
  /* -------------------------------------------------------------- */
  function head(sec, extra) {
    return '<header class="sec-head reveal">' +
      (sec.eyebrow ? '<p class="eyebrow">' + esc(sec.eyebrow) + "</p>" : "") +
      '<h2 class="sec-title">' + md(sec.title) + "</h2>" +
      '<div class="sec-rule">' + Lily.flower({ bloom: true, className: "is-closed tiny" }) + "</div>" +
      (sec.intro ? '<p class="sec-intro">' + md(sec.intro) + "</p>" : "") +
      (extra || "") + "</header>";
  }

  function buildOpening() {
    var o = C.opening;
    var nick = o.nicknames.map(function (n, i) { return '<span class="nick nick--' + (i % 4) + '" style="--d:' + (1.2 + i * 0.5) + 's">' + esc(n) + "</span>"; }).join("");
    $("#opening").innerHTML =
      Lily.sprig({ className: "corner corner--tl" }) + Lily.sprig({ className: "corner corner--tr" }) +
      '<div class="opening__inner">' +
        '<div class="opening__lily">' + Lily.flower({ bloom: true, className: "is-closed" }) + "</div>" +
        '<p class="opening__kicker">' + esc(o.kicker) + "</p>" +
        '<h1 class="opening__title"><span>' + esc(o.title) + '</span><span class="opening__name">' + esc(o.name) + "</span></h1>" +
        '<div class="nicks">' + nick + "</div>" +
        '<p class="opening__guj hand">' + esc(o.gujarati) + "</p>" +
        '<p class="opening__sub">' + md(o.sub) + "</p>" +
      "</div>" +
      '<p class="scrollcue"><span>' + esc(o.scrollCue) + '</span><i></i></p>';
    // opening lily blooms as soon as the site appears
    var l = $("#opening .lily");
    new MutationObserver(function (m, obs) { if (!$("#site").hidden) { setTimeout(function () { l.classList.remove("is-closed"); }, 200); obs.disconnect(); } })
      .observe($("#site"), { attributes: true });
  }

  function buildStory() {
    var s = C.story, m = s.map;
    var items = s.entries.map(function (e, i) {
      return '<li class="tl__item reveal' + (e.highlight ? " tl__item--hi" : "") + '" style="--i:' + i + '">' +
        '<span class="tl__dot">' + Lily.flower({ bloom: true, className: "is-closed tiny" }) + "</span>" +
        '<p class="tl__date">' + esc(e.date) + "</p>" +
        '<h3 class="tl__title">' + md(e.title) + "</h3>" +
        '<p class="tl__text">' + md(e.text) + "</p>" +
        (e.gujarati ? '<p class="tl__guj hand">' + esc(e.gujarati) + "</p>" : "") +
        "</li>";
    }).join("");
    var map =
      '<figure class="map reveal">' +
        '<figcaption class="map__title eyebrow">' + esc(m.title) + "</figcaption>" +
        '<svg class="map__svg" viewBox="0 0 320 360" role="img" aria-label="' + esc(m.from + " to " + m.to + ", " + m.distance) + '">' +
          '<path d="M0 0 H70 C 64 40, 92 70, 86 110 C 82 135, 104 150, 128 156 C 112 170, 126 186, 150 194 C 160 222, 168 250, 176 286 C 182 316, 190 340, 196 360 H0 Z" fill="#dcebf7" opacity=".75"/>' +
          '<path d="M70 0 C 64 40, 92 70, 86 110 C 82 135, 104 150, 128 156 C 112 170, 126 186, 150 194 C 160 222, 168 250, 176 286 C 182 316, 190 340, 196 360" fill="none" stroke="#b9d3ea" stroke-width="1.2"/>' +
          '<g fill="none" stroke="#c5dbef" stroke-width=".8" opacity=".9"><path d="M20 60 q10 -6 20 0 t20 0"/><path d="M30 230 q10 -6 20 0 t20 0"/><path d="M60 300 q10 -6 20 0 t20 0"/></g>' +
          '<text x="34" y="200" class="map__sea">Arabian Sea</text>' +
          '<path id="route" d="M206 300 C 250 240, 250 130, 194 72" fill="none" stroke="#c8414b" stroke-width="1.6" stroke-dasharray="2 6" stroke-linecap="round" class="map__route"/>' +
          (reduceMotion ? "" : '<text class="map__heart" font-size="14" text-anchor="middle" dy="5">🤍<animateMotion dur="5s" repeatCount="indefinite" rotate="0" keyPoints="0;1" keyTimes="0;1" calcMode="linear"><mpath href="#route" xlink:href="#route"/></animateMotion></text>') +
          '<g transform="translate(140 148)"><circle r="3.2" fill="#fff" stroke="#6f98bf" stroke-width="1.2"/></g>' +
          '<text x="130" y="146" text-anchor="end" class="map__small">' + esc(m.home) + "</text>" +
          '<text x="130" y="128" text-anchor="end" class="map__note">' + esc(m.homeNote) + "</text>" +
          '<g transform="translate(194 72)">' + '<circle r="16" fill="#fff" opacity=".8"/>' + '<g transform="translate(-14 -14) scale(.28)">' + Lily.flower({}).replace(/^<svg[^>]*>/, "").replace(/<\/svg>$/, "") + "</g></g>" +
          '<text x="218" y="64" class="map__city">' + esc(m.to) + "</text>" +
          '<text x="218" y="82" class="map__note">' + esc(m.toNote) + "</text>" +
          '<g transform="translate(206 300)">' + '<circle r="16" fill="#fff" opacity=".8"/>' + '<g transform="translate(-14 -14) scale(.28)">' + Lily.flower({}).replace(/^<svg[^>]*>/, "").replace(/<\/svg>$/, "") + "</g></g>" +
          '<text x="228" y="304" class="map__city">' + esc(m.from) + "</text>" +
          '<text x="228" y="322" class="map__note">' + esc(m.fromNote) + "</text>" +
          '<g transform="translate(262 190)"><rect x="-34" y="-14" width="68" height="26" rx="13" fill="#fff" stroke="#f0d3d3"/><text text-anchor="middle" y="5" class="map__km">' + esc(m.distance) + "</text></g>" +
          '<g transform="translate(286 30)" class="map__compass"><path d="M0 -12 L3 0 L0 12 L-3 0 Z" fill="#b9d3ea"/><path d="M0 -12 L3 0 L-3 0 Z" fill="#c8414b" opacity=".7"/><text y="-16" text-anchor="middle">N</text></g>' +
        "</svg>" +
        '<p class="map__caption hand">' + esc(m.caption) + "</p>" +
      "</figure>";
    $("#story").innerHTML = head(s) + '<ol class="tl">' + items + "</ol>" + map;
  }

  // Photos: files from the generated manifest; if there is none yet,
  // try 01.jpg, 02.jpg… for each caption. Missing files → soft placeholder.
  function photoList(key, captions) {
    var files = (PHOTOS[key] || []).slice();
    var list = [];
    var n = files.length || captions.length;
    for (var i = 0; i < n; i++) {
      var f = files[i];
      list.push({
        src: f ? f.src : "assets/photos/" + key + "/" + pad(i + 1) + ".jpg",
        w: f && f.w, h: f && f.h,
        caption: captions[i] || "",
      });
    }
    return list;
  }
  function polaroid(p, i, extraCls) {
    var tilt = [-2.2, 1.8, -1.2, 2.6, -2.8, 1.2, -1.6, 2.2, -2.4, 1.4][i % 10];
    return '<figure class="polaroid reveal ' + (extraCls || "") + '" style="--tilt:' + tilt + 'deg" tabindex="0" data-caption="' + esc(p.caption) + '">' +
      '<div class="polaroid__img">' +
        '<div class="polaroid__ph">' + Lily.flower({ className: "ph-lily" }) + '<span class="hand">' + esc(C.ui.photoMissing) + "</span></div>" +
        '<img src="' + esc(p.src) + '" alt="' + esc(p.caption) + '" loading="lazy" decoding="async"' + (p.w ? ' width="' + p.w + '" height="' + p.h + '"' : "") + ">" +
      "</div>" +
      (p.caption ? '<figcaption class="hand">' + md(p.caption) + "</figcaption>" : "") +
      "</figure>";
  }
  function wirePhotos(root) {
    $$(".polaroid img", root).forEach(function (img) {
      var fig = img.closest(".polaroid");
      var ok = function () { fig.classList.add("is-loaded"); };
      var bad = function () { fig.classList.add("is-missing"); img.remove(); };
      if (img.complete) { if (img.naturalWidth) ok(); else if (img.getAttribute("src")) { /* will error */ } }
      img.addEventListener("load", ok);
      img.addEventListener("error", bad);
    });
    $$(".polaroid", root).forEach(function (fig) {
      fig.addEventListener("click", function () { if (fig.classList.contains("is-loaded")) openLightbox(fig); });
    });
  }

  function buildGallery(id, key, sec) {
    var list = photoList(key, sec.captions);
    $("#" + id).innerHTML = head(sec) + '<div class="photos photos--' + key + '">' + list.map(function (p, i) { return polaroid(p, i); }).join("") + "</div>";
    wirePhotos($("#" + id));
  }

  function buildBloopers() {
    var b = C.bloopers, list = photoList("funny", b.captions);
    var ice = '<div class="joke joke--ice reveal">' +
        '<div class="ice" aria-hidden="true"><span>🧊</span><span>🧊</span><span>🧊</span></div>' +
        '<h3 class="joke__title">' + md(b.iceCubes.title) + "</h3>" +
        '<p class="joke__text">' + md(b.iceCubes.text) + "</p></div>";
    var chup = '<div class="joke joke--chat reveal">' +
        '<h3 class="joke__title">' + md(b.chup.title) + "</h3>" +
        '<div class="chat">' +
          '<p class="bubble bubble--her"><span class="bubble__who">RaRa</span>' + esc(b.chup.her) + "</p>" +
          '<p class="bubble bubble--me"><span class="bubble__who">Ash</span>' + esc(b.chup.me) + "</p>" +
          '<p class="chat__seen">Read 3:04 AM</p>' +
        "</div>" +
        '<p class="joke__note hand">' + esc(b.chup.note) + "</p></div>";
    var parts = [];
    list.forEach(function (p, i) {
      parts.push(polaroid(p, i + 3, "polaroid--tape"));
      if (i === 0) parts.push(ice);
      if (i === 1) parts.push(chup);
    });
    if (list.length < 2) { if (list.length < 1) parts.push(ice); parts.push(chup); }
    $("#bloopers").innerHTML = head(b) + '<div class="reel">' + parts.join("") + "</div>";
    wirePhotos($("#bloopers"));
  }

  function buildReasons() {
    var r = C.reasons;
    var cards = r.cards.map(function (c, i) {
      return '<button class="flip reveal" type="button" style="--i:' + (i % 2) + '" aria-pressed="false">' +
        '<span class="flip__inner">' +
          '<span class="flip__face flip__front"><span class="flip__num">' + pad(i + 1) + "</span>" + Lily.flower({ className: "flip__lily" }) + '<span class="flip__label">' + md(c.front) + '</span><span class="flip__tap">tap</span></span>' +
          '<span class="flip__face flip__back"><span class="flip__text hand">' + md(c.back) + "</span></span>" +
        "</span></button>";
    }).join("");
    $("#reasons").innerHTML = head(r) + '<div class="flips">' + cards + '</div><p class="reasons__done hand" hidden>' + md(r.allFlipped) + "</p>";
    var seen = {};
    $$("#reasons .flip").forEach(function (btn, i) {
      btn.addEventListener("click", function () {
        var on = btn.classList.toggle("is-flipped");
        btn.setAttribute("aria-pressed", String(on));
        seen[i] = true;
        if (Object.keys(seen).length === r.cards.length) {
          var d = $("#reasons .reasons__done");
          if (d.hidden) { d.hidden = false; requestAnimationFrame(function () { d.classList.add("in"); }); burstPetals(10, d); }
        }
      });
    });
  }

  function buildOpenWhen() {
    var o = C.openWhen;
    var env = o.letters.map(function (l, i) {
      return '<button class="envelope reveal" type="button" data-i="' + i + '" style="--i:' + i + '">' +
        '<span class="envelope__back"></span>' +
        '<span class="envelope__paper"></span>' +
        '<span class="envelope__front"></span>' +
        '<span class="envelope__flap"></span>' +
        '<span class="envelope__seal">' + Lily.flower({ className: "seal-lily" }) + "</span>" +
        '<span class="envelope__label"><small>open when</small>' + esc(l.label) + "</span>" +
        "</button>";
    }).join("");
    $("#openwhen").innerHTML = head(o) + '<div class="envelopes">' + env + "</div>";
    $$("#openwhen .envelope").forEach(function (b) {
      b.addEventListener("click", function () {
        b.classList.add("is-open");
        var l = o.letters[+b.getAttribute("data-i")];
        setTimeout(function () { openSheet(l); }, reduceMotion ? 0 : 650);
      });
    });
  }

  function buildLetter() {
    var l = C.letter;
    $("#letter").innerHTML =
      '<div class="paper">' +
        '<div class="paper__head reveal"><p class="eyebrow">' + esc(l.eyebrow) + '</p><h2 class="sec-title">' + md(l.title) + "</h2>" +
        '<div class="sec-rule">' + Lily.flower({ bloom: true, className: "is-closed tiny" }) + "</div></div>" +
        '<p class="paper__salute hand reveal">' + esc(l.salutation) + "</p>" +
        l.paragraphs.map(function (p) { return '<p class="paper__p reveal">' + md(p) + "</p>"; }).join("") +
        '<p class="paper__signoff reveal">' + esc(l.signoff) + '<span class="hand">' + esc(l.signature) + "</span></p>" +
        Lily.sprig({ className: "paper__sprig" }) +
      "</div>";
  }

  function buildNavratri() {
    var n = C.navratri;
    var after = now() >= NAVRATRI;
    var garland = '<div class="garland" aria-hidden="true">' +
      [0, 1, 2, 3, 4].map(function (i) { return i % 2 ? '<span class="g-lily">' + Lily.flower({}) + "</span>" : '<span class="g-diya">' + Lily.diya() + "</span>"; }).join("") + "</div>";
    $("#navratri").innerHTML =
      '<div class="festive reveal">' +
        '<div class="festive__rangoli" aria-hidden="true"></div>' +
        '<p class="eyebrow">' + esc(n.eyebrow) + "</p>" +
        '<h2 class="sec-title">' + esc(after ? n.afterTitle : n.title) + "</h2>" +
        garland +
        (after
          ? '<p class="festive__line">' + md(n.afterLine) + "</p>"
          : '<p class="festive__line">' + md(n.line) + "</p>" + countdownHTML(n.labels, "clock--small") +
            '<p class="festive__guj hand">' + esc(n.gujarati) + "</p>") +
        '<div class="dandiya" aria-hidden="true"><i></i><i></i></div>' +
      "</div>";
    if (!after) {
      var root = $("#navratri");
      var tick = function () {
        var left = NAVRATRI - now();
        if (left <= 0) { clearInterval(iv); buildNavratri(); $$("#navratri .reveal").forEach(function (e) { e.classList.add("in"); }); return; }
        paintClock(root, left);
      };
      var iv = setInterval(tick, 1000); tick();
    }
  }

  function cakeSVG() {
    var candles = "", i, x, y, k = 0;
    // back row (10) then front row (9) across the top of the cake
    var rows = [{ n: 10, y: 118, x0: 62, dx: 21.8 }, { n: 9, y: 132, x0: 72.9, dx: 21.8 }];
    rows.forEach(function (r) {
      for (i = 0; i < r.n; i++, k++) {
        x = r.x0 + i * r.dx; y = r.y;
        var c = k % 3 === 0 ? "#f3c9cb" : k % 3 === 1 ? "#cfe2f3" : "#fffaf2";
        candles += '<g class="candle" style="--k:' + k + '" transform="translate(' + x.toFixed(1) + " " + y + ')">' +
          '<rect x="-2.6" y="-30" width="5.2" height="30" rx="2" fill="' + c + '" stroke="#e8dccd" stroke-width=".5"/>' +
          '<path d="M-2.6 -24 L2.6 -27 M-2.6 -16 L2.6 -19 M-2.6 -8 L2.6 -11" stroke="#fff" stroke-width="1.1" opacity=".8"/>' +
          '<line x1="0" y1="-30" x2="0" y2="-34" stroke="#6b5a4a" stroke-width=".8"/>' +
          '<g class="candle__flame"><ellipse cx="0" cy="-40" rx="5" ry="8" fill="#fff3c8" opacity=".35"/><path d="M0 -47 C 4 -42, 4 -37, 0 -34 C -4 -37, -4 -42, 0 -47 Z" fill="url(#flame)"/></g>' +
          '<path class="candle__smoke" d="M0 -36 C -4 -44, 4 -50, 0 -58 C -3 -64, 3 -70, 0 -76" stroke="#c9ced6" stroke-width="1.4" fill="none" stroke-linecap="round"/>' +
          "</g>";
      }
    });
    return '<svg class="cake__svg" viewBox="0 0 320 280" role="img" aria-label="Birthday cake with 19 candles">' +
      '<ellipse cx="160" cy="266" rx="138" ry="10" fill="#000" opacity=".06"/>' +
      '<ellipse cx="160" cy="258" rx="140" ry="16" fill="#fff" stroke="#e8e1d6"/>' +
      // bottom tier
      '<path d="M44 178 V 240 C 44 258, 276 258, 276 240 V 178 Z" fill="#fdf7ef"/>' +
      '<path d="M44 200 C 70 214, 90 196, 112 210 C 134 222, 150 200, 172 212 C 196 224, 214 200, 236 212 C 256 222, 268 206, 276 204 V178 H44 Z" fill="#dcebf7"/>' +
      '<ellipse cx="160" cy="178" rx="116" ry="20" fill="#e8f2fb"/>' +
      // top tier
      '<path d="M66 128 V 176 C 66 192, 254 192, 254 176 V 128 Z" fill="#fffdf9"/>' +
      '<path d="M66 150 C 86 160, 100 146, 120 156 C 140 166, 156 148, 176 158 C 196 168, 214 148, 234 158 C 244 162, 250 156, 254 154 V128 H66 Z" fill="#f6dcdc"/>' +
      '<ellipse cx="160" cy="128" rx="94" ry="17" fill="#fff6f4" stroke="#f1dede"/>' +
      '<g fill="#c8414b" opacity=".8"><circle cx="80" cy="230" r="2.4"/><circle cx="120" cy="238" r="2.4"/><circle cx="160" cy="240" r="2.4"/><circle cx="200" cy="238" r="2.4"/><circle cx="240" cy="230" r="2.4"/></g>' +
      candles +
      '<g transform="translate(24 214) scale(.42)">' + Lily.flower({}).replace(/^<svg[^>]*>/, "").replace(/<\/svg>$/, "") + "</g>" +
      '<g transform="translate(254 210) scale(.36)">' + Lily.flower({}).replace(/^<svg[^>]*>/, "").replace(/<\/svg>$/, "") + "</g>" +
      "</svg>";
  }

  function buildWish() {
    var w = C.wish;
    $("#wish").innerHTML = head(w) +
      '<div class="cake reveal">' +
        '<button class="cake__btn" type="button" aria-label="' + esc(w.blowButton) + '">' + cakeSVG() + "</button>" +
        '<button class="btn btn--ghost cake__blow" type="button">' + esc(w.blowButton) + " 🌬️</button>" +
      "</div>" +
      '<form class="wishform" hidden>' +
        '<p class="wishform__lead">' + md(w.afterBlow) + "</p>" +
        '<textarea class="wishform__text hand" rows="4" maxlength="600" placeholder="' + esc(w.placeholder) + '"></textarea>' +
        '<a class="btn btn--primary wishform__send is-disabled" href="#" target="_blank" rel="noopener noreferrer" aria-disabled="true">' + esc(w.sendButton) + " 💌</a>" +
        '<p class="wishform__note hand">' + esc(w.note) + "</p>" +
      "</form>";
    var root = $("#wish"), blown = false;
    var blow = function () {
      if (blown) return; blown = true;
      root.classList.add("is-blown");
      $$(".candle", root).forEach(function (c) { c.style.setProperty("--t", (Math.random() * 700) + "ms"); });
      $(".cake__blow", root).classList.add("bye");
      setTimeout(function () { burstPetals(26, $(".cake", root)); }, 500);
      setTimeout(function () {
        var f = $(".wishform", root); f.hidden = false;
        requestAnimationFrame(function () { f.classList.add("in"); });
      }, reduceMotion ? 0 : 1400);
    };
    $(".cake__btn", root).addEventListener("click", blow);
    $(".cake__blow", root).addEventListener("click", blow);
    var ta = $(".wishform__text", root), send = $(".wishform__send", root);
    var num = String(S.whatsappNumber).replace(/\D/g, "");
    var update = function () {
      var v = ta.value.trim();
      send.href = "https://wa.me/" + num + "?text=" + encodeURIComponent(S.wishPrefix + v);
      send.classList.toggle("is-disabled", !v);
      send.setAttribute("aria-disabled", String(!v));
    };
    ta.addEventListener("input", update); update();
    send.addEventListener("click", function (e) { if (!ta.value.trim()) { e.preventDefault(); ta.focus(); } });
    $(".wishform", root).addEventListener("submit", function (e) { e.preventDefault(); });
  }

  function buildFinale() {
    var f = C.finale;
    $("#finale").innerHTML =
      '<div class="stars" aria-hidden="true"></div>' +
      '<div class="finale__inner reveal">' +
        '<p class="eyebrow">' + esc(f.eyebrow) + "</p>" +
        '<div class="finale__lily">' + Lily.flower({ bloom: true, className: "is-closed breathe" }) + "</div>" +
        '<p class="finale__intro">' + md(f.intro) + "</p>" +
        '<button class="player" type="button" aria-label="' + esc(f.playLabel) + '">' +
          '<svg viewBox="0 0 100 100" class="player__ring" aria-hidden="true"><circle cx="50" cy="50" r="46" class="player__track"/><circle cx="50" cy="50" r="46" class="player__prog"/></svg>' +
          '<span class="player__icon" aria-hidden="true"></span>' +
        "</button>" +
        '<p class="player__time" aria-hidden="true">0:00</p>' +
        '<p class="finale__line">' + md(f.line) + "</p>" +
        '<p class="finale__after hand">' + esc(f.afterLine) + "</p>" +
      "</div>";
    var stars = $("#finale .stars"), html = "";
    for (var i = 0; i < 36; i++) html += '<i style="left:' + (Math.random() * 100) + "%;top:" + (Math.random() * 100) + "%;--d:" + (Math.random() * 4) + "s;--s:" + (1 + Math.random() * 2) + 'px"></i>';
    stars.innerHTML = html;

    var btn = $("#finale .player"), v = Music.voice, prog = $("#finale .player__prog"), time = $("#finale .player__time");
    var C2 = 2 * Math.PI * 46;
    prog.style.strokeDasharray = C2; prog.style.strokeDashoffset = C2;
    var fmt = function (s) { s = Math.max(0, Math.floor(s || 0)); return Math.floor(s / 60) + ":" + pad(s % 60); };
    btn.addEventListener("click", function () {
      if (v.paused) { Music.playVoice(); } else { v.pause(); }
    });
    v.addEventListener("play", function () { btn.classList.add("is-playing"); btn.setAttribute("aria-label", f.pauseLabel); Music.updateButton(); });
    v.addEventListener("pause", function () { btn.classList.remove("is-playing"); btn.setAttribute("aria-label", f.playLabel); Music.updateButton(); });
    v.addEventListener("timeupdate", function () {
      var d = v.duration;
      if (d && isFinite(d)) { prog.style.strokeDashoffset = C2 * (1 - v.currentTime / d); time.textContent = fmt(v.currentTime) + " / " + fmt(d); }
      else time.textContent = fmt(v.currentTime);
    });
    v.addEventListener("ended", function () { $("#finale").classList.add("is-done"); burstPetals(20, $("#finale .finale__line")); });
    v.addEventListener("error", function () { time.textContent = "voice note coming soon 🤍"; });
  }

  /* -------------------------------------------------------------- */
  /* overlays: photo lightbox + letter sheet                         */
  /* -------------------------------------------------------------- */
  var lastFocus = null;
  function lockScroll(on) { document.documentElement.classList.toggle("no-scroll", on); }
  function openLightbox(fig) {
    var img = $("img", fig), lb = $("#lightbox");
    lastFocus = fig;
    lb.innerHTML = '<figure class="lightbox__fig"><img src="' + esc(img.currentSrc || img.src) + '" alt="' + esc(img.alt) + '">' +
      (fig.getAttribute("data-caption") ? '<figcaption class="hand">' + md(fig.getAttribute("data-caption")) + "</figcaption>" : "") +
      '</figure><p class="lightbox__hint">' + esc(C.ui.tapToClose) + "</p>";
    lb.hidden = false; lockScroll(true);
    requestAnimationFrame(function () { lb.classList.add("show"); });
    lb.onclick = function () { closeOverlay(lb); };
  }
  function openSheet(l) {
    var sh = $("#sheet");
    lastFocus = document.activeElement;
    sh.setAttribute("aria-label", l.title);
    sh.innerHTML = '<div class="sheet__scrim"></div><article class="sheet__paper" tabindex="-1">' +
      '<div class="sheet__lily">' + Lily.flower({}) + "</div>" +
      '<h3 class="sheet__title">' + md(l.title) + "</h3>" +
      l.body.map(function (p) { return "<p>" + md(p) + "</p>"; }).join("") +
      '<p class="sheet__sign hand">' + esc(l.signoff) + "</p>" +
      '<button class="btn btn--ghost sheet__close" type="button">' + esc(C.openWhen.close) + "</button></article>";
    sh.hidden = false; lockScroll(true);
    requestAnimationFrame(function () { sh.classList.add("show"); $(".sheet__paper", sh).focus({ preventScroll: true }); });
    $(".sheet__scrim", sh).onclick = $(".sheet__close", sh).onclick = function () { closeOverlay(sh); };
  }
  function closeOverlay(el) {
    el.classList.remove("show");
    setTimeout(function () { el.hidden = true; el.innerHTML = ""; lockScroll(false); if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true }); }, reduceMotion ? 0 : 380);
  }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { ["#lightbox", "#sheet"].forEach(function (s) { if (!$(s).hidden) closeOverlay($(s)); }); }
  });

  /* -------------------------------------------------------------- */
  /* boot                                                            */
  /* -------------------------------------------------------------- */
  function buildSite() {
    buildOpening();
    buildStory();
    buildGallery("now", "now", C.now);
    buildGallery("little", "little", C.little);
    buildBloopers();
    buildReasons();
    buildOpenWhen();
    buildLetter();
    buildNavratri();
    buildWish();
    buildFinale();
  }

  var mute = $("#mute");
  mute.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="note" d="M9 18.5a2.5 2.5 0 1 1-2.5-2.5c.6 0 1.1.2 1.5.5V6l10-2v11.5a2.5 2.5 0 1 1-2.5-2.5c.6 0 1.1.2 1.5.5V8.2L10 9.6v8.9z"/><path class="slash" d="M4 4l16 16" /></svg><span class="mute__bars" aria-hidden="true"><i></i><i></i><i></i></span>';
  mute.addEventListener("click", function (e) { e.stopPropagation(); Music.toggleMute(); });
  Music.updateButton();
  // safety net: any tap resumes music iOS may have paused (lock screen, calls…)
  document.addEventListener("touchend", function () { Music.kick(); }, { passive: true });
  document.addEventListener("click", function () { Music.kick(); });

  if (isPreview) {
    var badge = document.createElement("div");
    badge.className = "preview-badge"; badge.textContent = "preview";
    document.body.appendChild(badge);
  }
  if (S.whatsappNumber.indexOf("_") !== -1 && console && console.warn) console.warn("Set settings.whatsappNumber in js/content.js");

  buildSite();
  if (now() < BIRTHDAY && !isPreview) showCountdown();
  else showGate();
})();
