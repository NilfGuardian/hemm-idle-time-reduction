/* HEMM Idle Time Reduction — custom cursor ring, trail, spotlight and
   optional Three.js parallax. Works inside the Streamlit iframe and updates
   the main window (#spotlight + #hemm-3d-bg). */

(function () {
    'use strict';

    // Try to control the main Streamlit window; fall back to this iframe.
    let targetWin;
    try {
        targetWin = window.parent && window.parent.document ? window.parent : window;
    } catch (e) {
        targetWin = window;
    }
    const doc = targetWin.document;
    const root = doc.documentElement;

    const reduced = targetWin.matchMedia && targetWin.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ----------------------------------------------------------------------- //
    // Cursor ring + trail
    // ----------------------------------------------------------------------- //
    function buildCursor() {
        // Remove stale cursor/trail elements from a previous session so the
        // animation loop stays active.
        if (targetWin.__hemmCursorRAF) targetWin.cancelAnimationFrame(targetWin.__hemmCursorRAF);
        const staleCursor = doc.getElementById('hemm-cursor');
        if (staleCursor) staleCursor.remove();
        doc.querySelectorAll('.hemm-cursor-trail').forEach(function (el) { el.remove(); });

        const cursor = doc.createElement('div');
        cursor.id = 'hemm-cursor';
        cursor.innerHTML = '<div class="hemm-cursor-dot"></div>';
        cursor.setAttribute('aria-hidden', 'true');
        doc.body.appendChild(cursor);

        // Trail pool (max 8 elements to keep DOM light)
        const trailPool = [];
        const MAX_TRAIL = 8;
        for (let i = 0; i < MAX_TRAIL; i++) {
            const t = doc.createElement('div');
            t.className = 'hemm-cursor-trail';
            doc.body.appendChild(t);
            trailPool.push({ el: t, x: -100, y: -100, life: 0 });
        }

        // Show the cursor immediately (mouse may already be inside the window)
        cursor.style.opacity = '1';

        let mouseX = -100, mouseY = -100;
        let cursorX = -100, cursorY = -100;
        let lastTrail = 0;

        function onMove(e) {
            mouseX = e.clientX;
            mouseY = e.clientY;
            root.style.setProperty('--mouse-x', mouseX + 'px');
            root.style.setProperty('--mouse-y', mouseY + 'px');

            // Spotlight
            const spotlight = doc.getElementById('hemm-spotlight');
            if (spotlight) {
                spotlight.style.background =
                    'radial-gradient(circle 150px at ' + mouseX + 'px ' + mouseY + 'px, rgba(200, 230, 0, 0.035), transparent 70%)';
            }

            // Three.js parallax: nudge the existing scene group if we can reach it
            syncThreeScene(mouseX, mouseY);

            // Drop a trail element every ~30ms
            const now = performance.now();
            if (now - lastTrail > 30) {
                const slot = trailPool.reduce(function (a, b) { return a.life <= b.life ? a : b; });
                slot.x = mouseX;
                slot.y = mouseY;
                slot.life = 1.0;
                slot.el.style.transform = 'translate(' + mouseX + 'px,' + mouseY + 'px) scale(1)';
                slot.el.style.opacity = '0.55';
                lastTrail = now;
            }
        }

        function onEnter() { cursor.style.opacity = '1'; }
        function onLeave() { cursor.style.opacity = '0'; }

        // Interactive hover states
        function updateHover(e) {
            const target = e.target;
            const interactive = target.closest('button, a, .stButton>button, .stRadio, .stSelectbox, input, [role="button"]');
            cursor.classList.toggle('hemm-cursor--active', !!interactive);
        }

        function animate() {
            // Smooth cursor follow (linear interpolation)
            const k = 0.22;
            cursorX += (mouseX - cursorX) * k;
            cursorY += (mouseY - cursorY) * k;
            cursor.style.transform = 'translate(' + cursorX + 'px, ' + cursorY + 'px)';

            // Trail fade
            trailPool.forEach(function (slot) {
                if (slot.life > 0) {
                    slot.life -= 0.04;
                    const s = Math.max(0.2, slot.life);
                    slot.el.style.transform = 'translate(' + slot.x + 'px,' + slot.y + 'px) scale(' + s + ')';
                    slot.el.style.opacity = (slot.life * 0.55).toFixed(2);
                }
            });

            targetWin.__hemmCursorRAF = targetWin.requestAnimationFrame(animate);
        }

        targetWin.addEventListener('mousemove', onMove, { passive: true });
        doc.documentElement.addEventListener('mouseenter', onEnter);
        doc.documentElement.addEventListener('mouseleave', onLeave);
        doc.documentElement.addEventListener('mouseover', updateHover, { passive: true });

        targetWin.__hemmCursorRAF = targetWin.requestAnimationFrame(animate);
    }

    // ----------------------------------------------------------------------- //
    // Spotlight mount (created if missing)
    // ----------------------------------------------------------------------- //
    function ensureSpotlight() {
        if (doc.getElementById('hemm-spotlight')) return;
        const s = doc.createElement('div');
        s.id = 'hemm-spotlight';
        s.setAttribute('aria-hidden', 'true');
        doc.body.appendChild(s);
    }

    // ----------------------------------------------------------------------- //
    // Three.js parallax sync
    // ----------------------------------------------------------------------- //
    function syncThreeScene(mx, my) {
        if (!targetWin.__hemm_three_group) {
            // Try to find the Three group if the 3D scene is in the same window
            if (targetWin.hemmThreeGroup) {
                targetWin.__hemm_three_group = targetWin.hemmThreeGroup;
            } else if (targetWin.THREE) {
                // no exposed group reference; skip parallax
                return;
            }
        }
        const group = targetWin.__hemm_three_group;
        if (!group) return;

        const w = targetWin.innerWidth || 1;
        const h = targetWin.innerHeight || 1;
        const nx = (mx / w - 0.5) * 2;  // -1..1
        const ny = (my / h - 0.5) * 2;

        // Add a subtle target rotation; the scene's own animation loop blends
        group.userData.hemmTargetRotX = ny * (Math.PI / 36);  // ±5 deg
        group.userData.hemmTargetRotY = nx * (Math.PI / 36);
    }

    // ----------------------------------------------------------------------- //
    // Boot
    // ----------------------------------------------------------------------- //
    function boot() {
        if (targetWin.matchMedia && targetWin.matchMedia('(pointer: coarse)').matches) return; // skip on touch
        ensureSpotlight();
        buildCursor();
        // Tell CSS to hide default cursor on desktop
        if (doc.body) doc.body.classList.add('hemm-custom-cursor');
    }

    if (doc.readyState === 'loading') {
        doc.addEventListener('DOMContentLoaded', boot);
    } else {
        targetWin.setTimeout(boot, 350);
    }
})();
