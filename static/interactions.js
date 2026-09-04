/* HEMM Idle Time Reduction — scroll reveals and page-entrance orchestration.
   Kept lightweight and respectful of prefers-reduced-motion. */

(function () {
    'use strict';

    let topWin;
    try {
        topWin = window.parent && window.parent.document ? window.parent : window;
    } catch (e) {
        topWin = window;
    }
    const doc = topWin.document;
    const reduced = topWin.matchMedia && topWin.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ----------------------------------------------------------------------- //
    // Scroll reveals
    // ----------------------------------------------------------------------- //
    function bindScrollReveals() {
        // Re-bind every time the iframe is (re)loaded because the previous
        // IntersectionObserver lived in the old iframe's context.
        const selector = '.hemm-card, .hemm-hero, h1, h2, h3, [data-testid="stPlotlyChart"], .stDataFrame, [data-testid="stMetricValue"]';
        doc.querySelectorAll(selector).forEach(function (el) {
            if (el.classList.contains('hemm-card')) {
                if (!el.classList.contains('reveal')) el.classList.add('reveal');
                return;
            }
            if (/^H[1-6]$/.test(el.tagName)) {
                const dir = Array.from(el.parentElement ? el.parentElement.children : []).indexOf(el) % 2 === 0
                    ? 'reveal-left' : 'reveal-right';
                if (!el.classList.contains('reveal')) el.classList.add('reveal', dir);
                return;
            }
            if (!el.classList.contains('reveal')) {
                el.classList.add('reveal', 'reveal-scale');
            }
        });

        const observer = new topWin.IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    entry.target.querySelectorAll('.counter').forEach(startCounter);
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.12, rootMargin: '0px 0px -60px 0px' });

        doc.querySelectorAll('.reveal').forEach(function (el) { observer.observe(el); });

        // Safety net: if IntersectionObserver doesn't fire (e.g. tab switch,
        // small viewport, or elements already in view), force visible after 1.5s.
        topWin.setTimeout(function () {
            doc.querySelectorAll('.reveal:not(.visible)').forEach(function (el) {
                el.classList.add('visible');
                el.querySelectorAll('.counter').forEach(startCounter);
            });
        }, 1500);
    }

    // ----------------------------------------------------------------------- //
    // Number counter (kept here so it can be reused without three_setup.js)
    // ----------------------------------------------------------------------- //
    function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

    function startCounter(counter) {
        if (counter.dataset.counting === 'true' || counter.dataset.counted === 'true') return;
        counter.dataset.counting = 'true';
        const target = parseFloat(counter.dataset.target);
        if (!Number.isFinite(target)) { counter.dataset.counting = 'false'; return; }

        const decimals = parseInt(counter.dataset.decimals || '0', 10);
        const duration = reduced ? 0 : 1300;
        const startTime = topWin.performance.now();
        const prefix = counter.dataset.prefix || '';
        const sign = counter.dataset.sign || '';
        const suffix = counter.dataset.suffix || '';

        // Reset to 0 at animation start so it counts up visually
        counter.textContent = prefix + (0).toLocaleString('en-IN', {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        }) + sign + suffix;

        function step(now) {
            const p = Math.min((now - startTime) / duration, 1);
            const v = target * easeOutCubic(p);
            counter.textContent = prefix + v.toLocaleString('en-IN', {
                minimumFractionDigits: decimals,
                maximumFractionDigits: decimals,
            }) + sign + suffix;
            if (p < 1) {
                topWin.requestAnimationFrame(step);
            } else {
                counter.dataset.counting = 'false';
                counter.dataset.counted = 'true';
            }
        }
        topWin.requestAnimationFrame(step);
    }

    // ----------------------------------------------------------------------- //
    // Page entrance animation (run once per navigation, not on widget reruns)
    // ----------------------------------------------------------------------- //
    function playPageEntrance() {
        if (reduced) return;

        const main = doc.querySelector('section.main') || doc.querySelector('.stApp .main') || doc.querySelector('.stApp');
        if (!main) return;

        // Only play once per navigation to a new page, not on widget reruns.
        const currentPath = topWin.location.pathname + topWin.location.search;
        const flag = 'hemm_entrance_' + currentPath;
        if (topWin.sessionStorage && topWin.sessionStorage.getItem(flag)) return;
        if (topWin.sessionStorage) topWin.sessionStorage.setItem(flag, '1');

        main.classList.add('hemm-page-entrance');

        // Clean up animation class after it finishes so it doesn't replay on widget changes
        topWin.setTimeout(function () {
            main.classList.remove('hemm-page-entrance');
        }, 700);
    }

    // ----------------------------------------------------------------------- //
    // Boot
    // ----------------------------------------------------------------------- //
    function boot() {
        doc.body.classList.add('js-reveal-active');
        bindScrollReveals();
        playPageEntrance();
    }

    if (doc.readyState === 'loading') {
        doc.addEventListener('DOMContentLoaded', boot);
    } else {
        topWin.setTimeout(boot, 300);
    }

    // Re-run when Streamlit replaces page content (multipage navigation).
    // A MutationObserver on the main container catches new .reveal/.counter
    // elements that Streamlit injects on page change.
    function watchForNewContent() {
        const main = doc.querySelector('section.main') || doc.querySelector('.stApp .main') || doc.querySelector('.stApp');
        if (!main) {
            topWin.setTimeout(watchForNewContent, 500);
            return;
        }
        const mo = new topWin.MutationObserver(function (mutations) {
            let hasNew = false;
            for (const m of mutations) {
                if (m.addedNodes && m.addedNodes.length > 0) { hasNew = true; break; }
            }
            if (hasNew) {
                // Debounce: wait for Streamlit to finish rendering
                topWin.clearTimeout(watchForNewContent._t);
                watchForNewContent._t = topWin.setTimeout(function () {
                    bindScrollReveals();
                }, 200);
            }
        });
        mo.observe(main, { childList: true, subtree: true });
    }
    topWin.setTimeout(watchForNewContent, 500);
})();
