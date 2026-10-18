/* BorderlessGuard keynote runtime. Plain JS, no network, no dependencies.
   Model: current slide index + current build. Elements carry data attributes:
     data-b="n"      visible from build n on           data-until="n"  visible while build < n
     data-ex="n"     visible only at build n            data-cur="n"    highlighted at build n, dimmed otherwise */
(function () {
  'use strict';
  var stage = document.getElementById('stage');
  var slides = Array.prototype.slice.call(stage.querySelectorAll('.slide'));
  var N = slides.length;
  var cells = [];
  var cur = 0, build = 0, overview = false, sel = 0, digits = '', digitTimer = null;
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // wrap every slide in a cell (needed by the overview grid)
  slides.forEach(function (s, i) {
    var c = document.createElement('div');
    c.className = 'cell';
    stage.insertBefore(c, s);
    c.appendChild(s);
    var n = document.createElement('span');
    n.className = 'num';
    n.textContent = String(i + 1);
    c.appendChild(n);
    c.addEventListener('click', function () { if (overview) { sel = i; closeOverview(true); } });
    cells.push(c);
  });

  function builds(i) { return parseInt(slides[i].getAttribute('data-builds') || '0', 10); }

  // ---------------------------------------------------------------- stage scaling
  function fit() {
    if (overview) { layoutOverview(); return; }
    var w = window.innerWidth, h = window.innerHeight;
    var s = Math.min(w / 1920, h / 1080);
    var x = (w - 1920 * s) / 2, y = (h - 1080 * s) / 2;
    stage.style.transform = 'translate(' + x + 'px,' + y + 'px) scale(' + s + ')';
  }

  // ---------------------------------------------------------------- builds
  function applyBuild(slide, b, final) {
    var els = slide.querySelectorAll('[data-b],[data-until],[data-ex],[data-cur]');
    Array.prototype.forEach.call(els, function (e) {
      if (e.hasAttribute('data-b')) e.classList.toggle('on', b >= +e.getAttribute('data-b'));
      if (e.hasAttribute('data-until')) e.classList.toggle('on', b < +e.getAttribute('data-until'));
      if (e.hasAttribute('data-ex')) e.classList.toggle('on', b === +e.getAttribute('data-ex'));
      if (e.hasAttribute('data-cur')) {
        var on = b === +e.getAttribute('data-cur');
        e.classList.toggle('cur', on);
        e.classList.toggle('off', !on);
      }
    });
  }

  function render() {
    slides.forEach(function (s, i) {
      s.classList.toggle('active', i === cur);
      applyBuild(s, i === cur ? build : (i < cur ? builds(i) : 0));
    });
    var frac = builds(cur) ? build / builds(cur) : 1;
    var pct = ((cur + frac) / N) * 100;
    document.querySelector('#progress i').style.width = (cur === 0 && build === 0 ? 0 : pct) + '%';
    document.getElementById('counter').textContent = (cur + 1) + ' / ' + N;
    document.title = (cur + 1) + '/' + N + ' · BorderlessGuard';
    try { history.replaceState(null, '', '#' + (cur + 1) + (build ? '.' + build : '')); } catch (e) {}
    renderNotes();
  }

  function next() {
    if (overview) { sel = Math.min(N - 1, sel + 1); markSel(); return; }
    if (build < builds(cur)) { build++; }
    else if (cur < N - 1) { cur++; build = 0; }
    render();
  }
  function prev() {
    if (overview) { sel = Math.max(0, sel - 1); markSel(); return; }
    if (build > 0) { build--; }
    else if (cur > 0) { cur--; build = builds(cur); }
    render();
  }
  function go(i, b) { cur = Math.max(0, Math.min(N - 1, i)); build = b || 0; render(); }

  // ---------------------------------------------------------------- notes
  var notesEl = document.getElementById('notes');
  var t0 = null, timer = null;
  function fmt(sec) { sec = Math.max(0, Math.round(sec)); return Math.floor(sec / 60) + ':' + ('0' + (sec % 60)).slice(-2); }
  function renderNotes() {
    var data = (window.KEYNOTE_NOTES || {})[String(cur + 1)];
    notesEl.textContent = '';
    var h = document.createElement('div');
    h.className = 'nh';
    var b = document.createElement('b');
    b.textContent = 'Slide ' + (cur + 1) + ' · ' + slides[cur].getAttribute('data-title');
    h.appendChild(b);
    var meta = document.createElement('span');
    var bl = builds(cur);
    meta.textContent = (bl ? 'click ' + build + ' of ' + bl + ' · ' : '') + (data ? data.words + ' words · speech ' + data.from + ' to ' + data.to : '');
    h.appendChild(meta);
    var clock = document.createElement('span');
    clock.className = 'clock';
    clock.id = 'clock';
    h.appendChild(clock);
    notesEl.appendChild(h);
    if (data) {
      data.paras.forEach(function (segs) {
        var p = document.createElement('p');
        segs.forEach(function (sg) {
          var sp = document.createElement('span');
          sp.className = sg[0];
          sp.textContent = (sg[0] === 'do' ? '[' + sg[1] + '] ' : sg[1] + ' ');
          p.appendChild(sp);
        });
        notesEl.appendChild(p);
      });
    }
    tick();
  }
  function tick() {
    var c = document.getElementById('clock');
    if (!c) return;
    c.textContent = t0 ? 'elapsed ' + fmt((Date.now() - t0) / 1000) : 'timer starts at the first click';
  }
  function startTimer() { if (!t0) { t0 = Date.now(); timer = setInterval(tick, 1000); } }

  // ---------------------------------------------------------------- overview
  function layoutOverview() {
    var c = cells[0];
    var s = c.clientWidth / 1920;
    stage.style.setProperty('--ovs', s);
  }
  function markSel() {
    cells.forEach(function (c, i) { c.classList.toggle('sel', i === sel); });
    cells[sel].scrollIntoView({ block: 'nearest' });
  }
  function openOverview() {
    overview = true; sel = cur;
    document.body.classList.add('ov');
    slides.forEach(function (s, i) {
      applyBuild(s, builds(i));
      Array.prototype.forEach.call(s.querySelectorAll('[data-until]'), function (e) { e.classList.add('ovhide'); });
      Array.prototype.forEach.call(s.querySelectorAll('[data-ex]'), function (e) {
        e.classList.toggle('ovhide', +e.getAttribute('data-ex') !== builds(i));
      });
    });
    stage.style.transform = '';
    layoutOverview(); markSel();
  }
  function closeOverview(apply) {
    overview = false;
    document.body.classList.remove('ov');
    Array.prototype.forEach.call(stage.querySelectorAll('.ovhide'), function (e) { e.classList.remove('ovhide'); });
    if (apply) { cur = sel; build = 0; }
    fit(); render();
  }

  // ---------------------------------------------------------------- misc
  function toggleFull() {
    var d = document, el = d.documentElement;
    if (d.fullscreenElement) { d.exitFullscreen(); }
    else if (el.requestFullscreen) { el.requestFullscreen().catch(function () {}); }
  }
  function toast(t) {
    var g = document.getElementById('goto');
    g.textContent = t; g.classList.add('on');
    clearTimeout(digitTimer);
    digitTimer = setTimeout(function () { g.classList.remove('on'); digits = ''; }, 1800);
  }

  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var k = e.key;
    if (document.body.classList.contains('help') && k !== '?' ) { document.body.classList.remove('help'); if (k === 'Escape') return; }
    if (/^[0-9]$/.test(k)) { digits += k; toast('Go to slide ' + digits + '  ↵'); e.preventDefault(); return; }
    if (k === 'Enter') {
      if (digits) { var n = parseInt(digits, 10); digits = ''; toast('Slide ' + n); if (overview) { sel = Math.max(0, Math.min(N - 1, n - 1)); closeOverview(true); } else go(n - 1, 0); e.preventDefault(); return; }
      if (overview) { closeOverview(true); e.preventDefault(); return; }
    }
    switch (k) {
      case 'ArrowRight': case 'ArrowDown': case 'PageDown': case ' ': case 'Enter':
        e.preventDefault(); if (e.shiftKey && k === ' ') prev(); else { startTimer(); next(); } break;
      case 'ArrowLeft': case 'ArrowUp': case 'PageUp': case 'Backspace':
        e.preventDefault(); prev(); break;
      case 'Home': e.preventDefault(); if (overview) { sel = 0; markSel(); } else go(0, 0); break;
      case 'End': e.preventDefault(); if (overview) { sel = N - 1; markSel(); } else go(N - 1, builds(N - 1)); break;
      case 'f': case 'F': toggleFull(); break;
      case 'n': case 'N': document.body.classList.toggle('notes'); startTimer(); break;
      case 'o': case 'O': if (overview) closeOverview(false); else openOverview(); break;
      case 'b': case 'B': case '.': document.body.classList.toggle('blanked'); break;
      case '?': document.body.classList.toggle('help'); break;
      case 'Escape':
        if (overview) closeOverview(false);
        else if (document.body.classList.contains('notes')) document.body.classList.remove('notes');
        else if (document.body.classList.contains('blanked')) document.body.classList.remove('blanked');
        break;
    }
  });

  // click on the stage advances (like a keynote); clicks on the nav buttons and the notes do not
  document.getElementById('viewport').addEventListener('click', function (e) {
    if (overview) return;
    if (e.target.closest && e.target.closest('#notes, button')) return;
    startTimer(); next();
  });
  document.getElementById('prev').addEventListener('click', function (e) { e.stopPropagation(); prev(); });
  document.getElementById('next').addEventListener('click', function (e) { e.stopPropagation(); startTimer(); next(); });
  document.getElementById('help').addEventListener('click', function () { document.body.classList.remove('help'); });

  // cursor and nav buttons hide when idle
  var idle;
  function moved() {
    document.body.classList.add('moved'); document.body.classList.remove('idle');
    clearTimeout(idle);
    idle = setTimeout(function () { document.body.classList.remove('moved'); document.body.classList.add('idle'); }, 2600);
  }
  document.addEventListener('mousemove', moved);

  window.addEventListener('resize', fit);
  window.addEventListener('hashchange', function () { readHash(); render(); });
  function readHash() {
    var m = /^#(\d+)(?:\.(\d+))?$/.exec(location.hash);
    if (m) { cur = Math.max(0, Math.min(N - 1, +m[1] - 1)); build = Math.min(+(m[2] || 0), builds(cur)); }
  }

  // facts that must match a live run are checked at build time by build_keynote.py
  readHash();
  fit();
  render();
  document.body.classList.add('ready');
})();
