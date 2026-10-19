/* BorderlessGuard film mode runtime. Plain JS, no network, no dependencies.
   The picture is a pure function of the clock t (seconds): every element with data-cue / data-until-cue is shown or hidden
   by comparing t with the start of the narration sentence it refers to (film_data.js, generated from segments.json), and
   every .count number is computed from t. So it plays, pauses, seeks and records identically every time.

   URL options:  ?scene=05   play only that segment (id), hold the last frame     #t=95.5   start at 95.5 s
                 ?captions=1 captions on      ?slates=0  blank slates (OBS segments are black)
                 ?bar=0      no control bar   ?autoplay=1 start without a click (for screen recording scripts)
   Keys: Space play/pause, Left/Right previous/next scene, Home restart, C captions, F full screen. */
(function () {
  'use strict';
  var D = window.FILM_DATA, scenes = D.scenes, TOTAL = D.total;
  var stage = document.getElementById('stage');
  var q = new URLSearchParams(location.search);
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var LEAD = 0.15;                       // reveal a touch before the first word
  var t = 0, playing = false, started = false, last = 0, solo = null;

  function fmt1(x) { x = Math.round(x * 10) / 10; var m = Math.floor(x / 60); return m + ':' + (x - 60 * m < 10 ? '0' : '') + (x - 60 * m).toFixed(1); }
  function fmt(x) { x = Math.max(0, x); return Math.floor(x / 60) + ':' + ('0' + Math.floor(x % 60)).slice(-2); }

  // ---------------------------------------------------------------- slates for the OBS segments
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  scenes.forEach(function (s) {
    if (s.kind !== 'obs') return;
    var sec = el('section', 'slide scene slate'); sec.setAttribute('data-seg', s.id);
    var ey = el('p', 'eyebrow rec'); ey.appendChild(el('i')); ey.appendChild(document.createTextNode('Cut to the OBS recording of the real app · segment ' + s.id + ' · ' + s.part));
    sec.appendChild(ey);
    sec.appendChild(el('h2', 'title', s.title));
    if (s.button) sec.appendChild(el('p', 'lead', s.button));
    sec.appendChild(el('p', 'meta', 'Slot ' + fmt1(s.start) + ' to ' + fmt1(s.end) + ' · voice starts at ' + fmt1(s.vstart)));
    if (s.action) sec.appendChild(el('p', 'act', s.action));
    // slates sit between the film scenes in the document order of their start time
    var after = null;
    scenes.forEach(function (o) { if (o.kind === 'film' && o.start < s.start) after = o; });
    var ref = stage.querySelector('.scene[data-seg="' + (after ? after.id : '') + '"]');
    var nextSibling = ref ? ref.nextElementSibling : stage.firstElementChild;
    stage.insertBefore(sec, nextSibling);
  });
  if (q.get('slates') === '0') document.body.classList.add('noslates');
  if (q.get('bar') === '0') document.body.classList.add('nobar');
  if (q.get('captions') === '1') document.body.classList.add('cc');

  var byId = {}, sceneEls = {};
  scenes.forEach(function (s) { byId[s.id] = s; });
  Array.prototype.forEach.call(stage.querySelectorAll('.scene'), function (e) { sceneEls[e.getAttribute('data-seg')] = e; });
  scenes.forEach(function (s) { if (!sceneEls[s.id]) throw new Error('no markup for segment ' + s.id); });

  // ---------------------------------------------------------------- cue bookkeeping
  var items = [];                          // elements that depend on the clock
  scenes.forEach(function (s) {
    var root = sceneEls[s.id];
    Array.prototype.forEach.call(root.querySelectorAll('[data-cue],[data-until-cue],.count'), function (e) {
      var it = { el: e, s: s, count: e.classList.contains('count') };
      var base = e.closest('[data-cue]');
      var own = e.hasAttribute('data-delay') ? parseFloat(e.getAttribute('data-delay')) : 0;
      if (e.hasAttribute('data-cue')) {
        var k = +e.getAttribute('data-cue');
        if (k >= s.cues.length) throw new Error('segment ' + s.id + ' has no sentence ' + k);
        it.on = s.cues[k].t - LEAD + own;
      } else if (base) {
        var kb = +base.getAttribute('data-cue');
        it.on = s.cues[kb].t - LEAD + (base.hasAttribute('data-delay') ? parseFloat(base.getAttribute('data-delay')) : 0) + own;
      } else {
        it.on = s.vstart + own;
      }
      if (e.hasAttribute('data-until-cue')) {
        var ku = +e.getAttribute('data-until-cue');
        if (ku >= s.cues.length) throw new Error('segment ' + s.id + ' has no sentence ' + ku);
        it.off = s.cues[ku].t - LEAD;
      }
      if (it.count) {
        it.to = parseFloat(e.getAttribute('data-to'));
        it.dec = +(e.getAttribute('data-dec') || 0);
        it.pre = e.getAttribute('data-prefix') || '';
        it.suf = e.getAttribute('data-suffix') || '';
        it.dur = parseFloat(e.getAttribute('data-dur') || '1.4');
        it.on = (e.hasAttribute('data-cue') || base) ? it.on : s.vstart;
      }
      items.push(it);
    });
  });

  function ease(x) { return 1 - Math.pow(1 - x, 3); }
  function numText(it, v) {
    return it.pre + v.toLocaleString('en-US', { minimumFractionDigits: it.dec, maximumFractionDigits: it.dec }) + it.suf;
  }

  // ---------------------------------------------------------------- one frame
  var active = null;
  var capEl = document.getElementById('captions');
  function apply() {
    var idx = 0;
    for (var i = 0; i < scenes.length; i++) if (scenes[i].start <= t + 1e-6) idx = i;
    var cur = scenes[idx];
    if (active !== cur.id) {
      scenes.forEach(function (s) { sceneEls[s.id].classList.toggle('active', s.id === cur.id); });
      active = cur.id;
    }
    items.forEach(function (it) {
      if (it.count) {
        var x = reduce ? (t >= it.on ? 1 : 0) : Math.min(1, Math.max(0, (t - it.on) / it.dur));
        it.el.textContent = numText(it, it.to * ease(x));
        return;
      }
      it.el.classList.toggle('on', t >= it.on && (it.off === undefined || t < it.off));
    });
    // captions
    var subs = D.subs || [], txt = '';
    for (var j = 0; j < subs.length; j++) if (t >= subs[j].t && t <= subs[j].end + 0.25) { txt = subs[j].text; break; }
    capEl.textContent = txt;
    capEl.classList.toggle('has', !!txt);
    // chrome
    var range = solo ? [solo.start, solo.end] : [0, TOTAL];
    document.getElementById('btime').textContent = fmt(t) + ' / ' + fmt(TOTAL);
    document.getElementById('bseek').value = t;
    document.getElementById('bscene').textContent = cur.id + ' · ' + cur.title;
    document.getElementById('filmprog').style.width = (100 * (t - range[0]) / Math.max(0.1, range[1] - range[0])) + '%';
    document.title = fmt(t) + ' · BorderlessGuard film';
  }

  // ---------------------------------------------------------------- clock
  function setPlaying(p) {
    playing = p;
    document.body.classList.toggle('playing', p);
    document.getElementById('bplay').innerHTML = p ? '&#10074;&#10074;' : '&#9654;';
    last = 0;
  }
  function seek(x) {
    var lo = solo ? solo.start : 0, hi = solo ? solo.end : TOTAL;
    t = Math.max(lo, Math.min(hi, x));
    apply();
  }
  function frame(now) {
    if (playing) {
      if (last) t += (now - last) / 1000;
      last = now;
      var hi = solo ? solo.end : TOTAL;
      if (t >= hi) { t = hi; setPlaying(false); }
      apply();
    }
    requestAnimationFrame(frame);
  }
  function play() {
    started = true; document.body.classList.add('started');
    var hi = solo ? solo.end : TOTAL;
    if (t >= hi - 0.05) t = solo ? solo.start : 0;
    setPlaying(true);
  }
  function toggle() { if (playing) setPlaying(false); else play(); }
  function sceneIndex() { var i = 0; scenes.forEach(function (s, k) { if (s.start <= t + 1e-6) i = k; }); return i; }
  function nextScene() { var i = sceneIndex(); if (i < scenes.length - 1) seek(scenes[i + 1].start); }
  function prevScene() { var i = sceneIndex(); seek(t - scenes[i].start > 1.5 || i === 0 ? scenes[i].start : scenes[i - 1].start); }
  function restart() { seek(solo ? solo.start : 0); }

  // ---------------------------------------------------------------- stage scaling
  function fit() {
    var w = window.innerWidth, h = window.innerHeight, s = Math.min(w / 1920, h / 1080);
    stage.style.transform = 'translate(' + (w - 1920 * s) / 2 + 'px,' + (h - 1080 * s) / 2 + 'px) scale(' + s + ')';
  }
  window.addEventListener('resize', fit);

  // ---------------------------------------------------------------- UI
  var bar = document.getElementById('bseek');
  bar.max = TOTAL;
  var prog = document.createElement('div'); prog.id = 'filmprog'; document.body.appendChild(prog);
  document.getElementById('bplay').addEventListener('click', toggle);
  document.getElementById('bprev').addEventListener('click', prevScene);
  document.getElementById('bnext').addEventListener('click', nextScene);
  document.getElementById('brestart').addEventListener('click', restart);
  bar.addEventListener('input', function () { seek(parseFloat(bar.value)); });
  var cc = document.getElementById('bcc');
  function setCC(on) { document.body.classList.toggle('cc', on); cc.setAttribute('aria-pressed', on ? 'true' : 'false'); }
  cc.addEventListener('click', function () { setCC(!document.body.classList.contains('cc')); });
  function toggleFull() { var d = document; if (d.fullscreenElement) d.exitFullscreen(); else if (d.documentElement.requestFullscreen) d.documentElement.requestFullscreen().catch(function () {}); }
  document.getElementById('bfull').addEventListener('click', toggleFull);
  document.getElementById('startbtn').addEventListener('click', function (e) { e.stopPropagation(); play(); });
  document.getElementById('start').addEventListener('click', play);
  setCC(document.body.classList.contains('cc'));

  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var onBar = e.target && e.target.closest && e.target.closest('#bar');
    switch (e.key) {
      case ' ': if (onBar && e.target.tagName === 'BUTTON') return; e.preventDefault(); toggle(); break;
      case 'ArrowRight': if (e.target && e.target.id === 'bseek') return; e.preventDefault(); nextScene(); break;
      case 'ArrowLeft': if (e.target && e.target.id === 'bseek') return; e.preventDefault(); prevScene(); break;
      case 'Home': e.preventDefault(); restart(); break;
      case 'c': case 'C': setCC(!document.body.classList.contains('cc')); break;
      case 'f': case 'F': toggleFull(); break;
    }
  });
  var idle;
  document.addEventListener('mousemove', function () {
    document.body.classList.remove('idle'); clearTimeout(idle);
    idle = setTimeout(function () { if (playing) document.body.classList.add('idle'); }, 2500);
  });

  // ---------------------------------------------------------------- start
  var sid = q.get('scene');
  if (sid) {
    sid = ('0' + sid).slice(-2);
    if (!byId[sid]) throw new Error('unknown segment ' + sid);
    solo = { start: byId[sid].start, end: byId[sid].end };
    t = solo.start;
  }
  var m = /^#t=([\d.]+)/.exec(location.hash);
  if (m) t = Math.max(0, Math.min(TOTAL, parseFloat(m[1])));
  fit();
  apply();
  requestAnimationFrame(frame);
  window.film = { seek: seek, play: play, pause: function () { setPlaying(false); }, time: function () { return t; }, total: TOTAL, scenes: scenes };
  document.body.classList.add('ready');
  if (q.get('autoplay') === '1') play();
})();
