/* HEMM Idle Time Reduction — lightweight Three.js live-art background + scroll/counter orchestration.
   Loaded inside a st.components.v1.html iframe. It tries to mount the canvas into the
   parent window's #hemm-3d-bg div; if that fails it renders inside the iframe itself. */

(function () {
    'use strict';

    // ----------------------------------------------------------------------- //
    // Pick the document we will paint and animate in. In Streamlit's iframe
    // srcdoc, window.parent is the main app and is same-origin.
    // ----------------------------------------------------------------------- //
    let topWin;
    try {
        topWin = window.parent;
        if (!topWin || !topWin.document) topWin = window;
    } catch (e) {
        topWin = window;
    }

    const doc = topWin.document;
    const reduced = topWin.matchMedia && topWin.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ----------------------------------------------------------------------- //
    // Three.js scene: low-poly wireframe icosahedron + muted particle field
    // ----------------------------------------------------------------------- //
    function init3D() {
        let container = doc.getElementById('hemm-3d-bg');

        // Cancel any previous animation loop and remove stale canvas so we don't
        // pile up background renderers when the Streamlit script reruns.
        if (topWin.__hemmThreeRAF) topWin.cancelAnimationFrame(topWin.__hemmThreeRAF);
        const stale = container ? container.querySelector('canvas') : null;
        if (stale) stale.remove();

        // Fallback: render inside the iframe body if the mount-point is missing
        if (!container) {
            if (topWin !== window) {
                // parent document is unreachable or mount missing; render locally
                container = window.document.createElement('div');
                container.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:0;overflow:hidden;';
                window.document.body.appendChild(container);
            } else {
                return; // nothing to draw on
            }
        }

        const w = container.clientWidth || topWin.innerWidth || 800;
        const h = container.clientHeight || topWin.innerHeight || 600;

        const scene = new topWin.THREE.Scene();
        const camera = new topWin.THREE.PerspectiveCamera(60, w / h, 0.1, 1000);
        camera.position.set(0, 0, 10);

        const renderer = new topWin.THREE.WebGLRenderer({ alpha: true, antialias: true });
        renderer.setSize(w, h);
        renderer.setPixelRatio(Math.min(topWin.devicePixelRatio || 1, 2));
        renderer.setClearColor(0x000000, 0);
        container.appendChild(renderer.domElement);

        const group = new topWin.THREE.Group();
        scene.add(group);

        // Expose for mouse parallax
        group.userData.baseRotX = 0;
        group.userData.baseRotY = 0;
        topWin.hemmThreeGroup = group;

        // Wireframe low-poly dodecahedron-ish form in the accent lime
        const geometry = new topWin.THREE.IcosahedronGeometry(2.8, 0);
        const wireframe = new topWin.THREE.WireframeGeometry(geometry);
        const line = new topWin.THREE.LineSegments(
            wireframe,
            new topWin.THREE.LineBasicMaterial({
                color: 0xC8E600,
                transparent: true,
                opacity: 0.38,
            })
        );
        group.add(line);

        // Muted floating particles
        const n = 100;
        const positions = new Float32Array(n * 3);
        for (let i = 0; i < n * 3; i++) {
            positions[i] = (Math.random() - 0.5) * 35;
        }
        const pGeo = new topWin.THREE.BufferGeometry();
        pGeo.setAttribute('position', new topWin.THREE.BufferAttribute(positions, 3));
        const pMat = new topWin.THREE.PointsMaterial({
            color: 0x858388,
            size: 0.07,
            transparent: true,
            opacity: 0.5,
        });
        const points = new topWin.THREE.Points(pGeo, pMat);
        group.add(points);

        let last = 0;
        const fps = reduced ? 0 : 30;

        function onFrame(t) {
            topWin.__hemmThreeRAF = topWin.requestAnimationFrame(onFrame);
            if (fps && t - last < 1000 / fps) return;
            last = t;
            group.userData.baseRotX += 0.0012;
            group.userData.baseRotY += 0.0018;
            points.rotation.y -= 0.0004;

            // Mouse-driven parallax tilt (max ±5 degrees)
            const tiltX = group.userData.hemmTargetRotX || 0;
            const tiltY = group.userData.hemmTargetRotY || 0;
            group.rotation.x = group.userData.baseRotX + tiltX;
            group.rotation.y = group.userData.baseRotY + tiltY;

            renderer.render(scene, camera);
        }
        topWin.__hemmThreeRAF = topWin.requestAnimationFrame(onFrame);

        topWin.addEventListener('resize', function () {
            const nw = container.clientWidth || topWin.innerWidth;
            const nh = container.clientHeight || topWin.innerHeight;
            camera.aspect = nw / nh;
            camera.updateProjectionMatrix();
            renderer.setSize(nw, nh);
        });
    }

    function loadThree() {
        if (typeof topWin.THREE !== 'undefined') {
            init3D();
            return;
        }
        const s = doc.createElement('script');
        s.src = 'https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js';
        s.onload = init3D;
        s.onerror = function () {
            // CDN failed: leave the CSS isometric grid as fallback
            // eslint-disable-next-line no-console
            console.warn('HEMM 3D: could not load Three.js');
        };
        (doc.head || doc.body).appendChild(s);
    }

    function boot() {
        if (!reduced) {
            loadThree();
        }
    }

    if (doc.readyState === 'loading') {
        doc.addEventListener('DOMContentLoaded', boot);
    } else {
        // Streamlit re-renders the DOM after load; give it a moment
        topWin.setTimeout(boot, 300);
    }
})();
