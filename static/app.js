/* Bookshelf client: theme, mobile menu, and (on the library page) live filtering.
   State lives in the URL (?view=series&status=read&q=...) so every view is linkable.
   Attribute contract with templates/_macros.html: data-item, data-status, data-kind, data-year,
   data-types (JSON), data-series (JSON), data-search, data-pages, data-date, data-title, data-author. */
(function () {
  'use strict';

  var doc = document.documentElement;
  var $ = function (sel, scope) { return (scope || document).querySelector(sel); };
  var $$ = function (sel, scope) { return Array.prototype.slice.call((scope || document).querySelectorAll(sel)); };
  var root = document.body.getAttribute('data-root') || '';
  var home = document.body.getAttribute('data-home') || root;
  var isManga = document.body.getAttribute('data-collection') === 'manga';

  /* ---------- theme ---------- */
  var themeLabel = $('#theme-label');
  function paintTheme() { themeLabel.textContent = doc.getAttribute('data-theme'); }
  paintTheme();
  $('#theme').addEventListener('click', function () {
    var next = doc.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    doc.setAttribute('data-theme', next);
    try { localStorage.setItem('theme', next); } catch (e) { /* storage unavailable */ }
    paintTheme();
  });

  /* Year charts open on the newest year and fade the edge that has more years beyond it. */
  $$('[data-scroll-end]').forEach(function (el) {
    function fade() {
      el.style.setProperty('--fl', el.scrollLeft > 2 ? '28px' : '0px');
      el.style.setProperty('--fr', el.scrollLeft + el.clientWidth < el.scrollWidth - 2 ? '28px' : '0px');
    }
    el.scrollLeft = el.scrollWidth;
    fade();
    el.addEventListener('scroll', fade, { passive: true });
    window.addEventListener('resize', fade);
  });

  var search = $('#q');
  var clearSearch = $('#search-clear');
  var library = $('#library');

  function updateClearSearch() {
    clearSearch.hidden = !search.value;
  }
  search.addEventListener('input', updateClearSearch);
  clearSearch.addEventListener('click', function () {
    search.value = '';
    search.dispatchEvent(new Event('input', { bubbles: true }));
    search.focus();
  });

  /* Off the library page, the search box lists matching books from search.json in the
     dropdown that lives next to it (see base.html). Enter (or "open in library")
     continues the search on the library page. */
  if (!library) {
    var results = $('#search-results');

    var index = null, loading = false;
    var esc = function (t) {
      return String(t).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
      });
    };
    var show = function () {
      var q = search.value.trim().toLowerCase();
      results.hidden = !q;
      if (!q || !index) return;
      var hits = index.filter(function (b) { return !!b.m === isManga && b.k.indexOf(q) >= 0; });
      if (!hits.length) { results.innerHTML = '<p class="empty">no matches</p>'; return; }
      results.innerHTML = hits.slice(0, 8).map(function (b) {
        return '<a class="note-link result" href="' + root + 'books/' + esc(b.s) + '/"><span>' + esc(b.t) + '</span><small>' + esc(b.a) + '</small></a>';
      }).join('') +
        '<a class="note-link see-all" href="' + home + '?q=' + encodeURIComponent(q) + '">' +
        (hits.length > 8 ? 'all ' + hits.length + ' in ' : 'open in ') + (isManga ? 'manga' : 'library') + ' →</a>';
    };
    var load = function () {
      if (index || loading) return;
      loading = true;
      fetch(root + 'search.json')
        .then(function (r) { return r.json(); })
        .then(function (data) { index = data; show(); })
        .catch(function () { /* Enter still works without the index */ })
        .then(function () { loading = false; });
    };

    search.addEventListener('focus', load);
    search.addEventListener('input', function () { load(); show(); });
    search.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && search.value.trim()) location.href = home + '?q=' + encodeURIComponent(search.value.trim());
      if (e.key === 'Escape') {
        search.value = '';
        updateClearSearch();
        show();
      }
    });
    return;
  }

  /* ---------- library ---------- */
  var VIEWS = ['covers', 'series'];
  var FILTERS = ['kind', 'status', 'year', 'type', 'series'];
  var state = { view: 'covers', q: '', kind: '', status: '', year: '', type: '', series: '' };

  var params = new URLSearchParams(location.search);
  Object.keys(state).forEach(function (k) { if (params.has(k)) state[k] = params.get(k); });
  if (VIEWS.indexOf(state.view) < 0) state.view = 'covers';
  search.value = state.q;
  updateClearSearch();

  var items = $$('[data-item]');
  items.forEach(function (el) {
    el._types = JSON.parse(el.getAttribute('data-types') || '[]');
    el._series = JSON.parse(el.getAttribute('data-series') || '[]');
  });
  var coverItems = $$('[data-item]', $('#view-covers'));

  function matches(el) {
    var q = state.q.trim().toLowerCase();
    return (!state.kind || el.getAttribute('data-kind') === state.kind) &&
      (!state.status || el.getAttribute('data-status') === state.status) &&
      (!state.year || el.getAttribute('data-year') === state.year) &&
      (!state.type || el._types.indexOf(state.type) >= 0) &&
      (!state.series || el._series.indexOf(state.series) >= 0) &&
      (!q || el.getAttribute('data-search').indexOf(q) >= 0);
  }

  function apply() {
    VIEWS.forEach(function (v) { $('#view-' + v).hidden = v !== state.view; });
    $$('.pill[data-view]').forEach(function (p) {
      var on = p.getAttribute('data-view') === state.view;
      p.classList.toggle('is-active', on);
      p.setAttribute('aria-pressed', String(on));
    });

    var active = $('#view-' + state.view);
    var shown = 0;
    $$('[data-item]', active).forEach(function (el) {
      var ok = matches(el);
      el.hidden = !ok;
      if (ok) shown += 1;
    });
    $$('.subseries', active).forEach(function (sg) { sg.hidden = !$('[data-item]:not([hidden])', sg); });
    $$('.group', active).forEach(function (g) { g.hidden = !$('[data-item]:not([hidden])', g); });
    $('#empty').hidden = shown > 0;

    /* header stats reflect the current filter, counted once per book */
    var vis = coverItems.filter(matches);
    var read = vis.filter(function (el) { return el.getAttribute('data-status') === 'read'; });
    var pages = read.reduce(function (n, el) { return n + (parseInt(el.getAttribute('data-pages'), 10) || 0); }, 0);
    $('#lib-meta').textContent = vis.length + ' ' + (library.getAttribute('data-noun') || 'books') + ' · ' + read.length + ' read · ' + pages.toLocaleString('en-US') + ' pages';

    var anyFilter = !!state.q || FILTERS.some(function (k) { return state[k]; });
    var clearBtn = $('#clear-filters');
    if (clearBtn) clearBtn.hidden = !anyFilter;
    $$('[data-filter]').forEach(function (a) {
      a.classList.toggle('is-active', state[a.getAttribute('data-filter')] === a.getAttribute('data-value'));
    });
  }

  function persist() {
    var p = new URLSearchParams();
    Object.keys(state).forEach(function (k) {
      var v = state[k];
      if (!v || (k === 'view' && v === 'covers')) return;
      p.set(k, v);
    });
    var qs = p.toString();
    history.replaceState(null, '', location.pathname + (qs ? '?' + qs : ''));
  }

  function update() { apply(); persist(); }

  document.addEventListener('click', function (e) {
    var pill = e.target.closest('.pill[data-view]');
    if (pill) {
      state.view = pill.getAttribute('data-view');
      if (state.view === 'covers') state.series = '';
      update();
      return;
    }

    var link = e.target.closest('a[data-filter]');
    if (link) {
      e.preventDefault();
      var key = link.getAttribute('data-filter'), value = link.getAttribute('data-value');
      state[key] = state[key] === value ? '' : value;
      update();
      return;
    }

    if (e.target.closest('#clear-filters')) {
      e.preventDefault();
      state.q = '';
      FILTERS.forEach(function (k) { state[k] = ''; });
      search.value = '';
      update();
    }
  });

  search.addEventListener('input', function () { state.q = search.value; update(); });

  apply();
})();