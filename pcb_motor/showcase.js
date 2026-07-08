/* pcb-motor showcase page runtime.
 *
 * Everything renders from the single JSON blob embedded in #motor-data:
 *   - motor animation (canvas): rotor magnets + rotating Biot-Savart field over
 *     the real coil artwork, coils tinted by commutated phase current
 *   - copper viewer (SVG): zoom/pan the board artwork with layer toggles
 *   - exploded stack (SVG): the axial sandwich, to scale
 *   - charts (SVG): sweep trade-offs, Bz profile, torque vs angle
 *
 * No libraries, no network. Light/dark follow the CSS custom properties; on a
 * scheme change everything is rebuilt with the new tokens.
 */
(function () {
  "use strict";

  var DATA = JSON.parse(document.getElementById("motor-data").textContent);
  var REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------- tokens & tiny helpers ---------------- */

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function tokens() {
    return {
      surface: cssVar("--surface"), page: cssVar("--page"),
      ink: cssVar("--ink"), ink2: cssVar("--ink-2"), muted: cssVar("--muted"),
      grid: cssVar("--grid"), baseline: cssVar("--baseline"),
      border: cssVar("--border"),
      series: [cssVar("--series-1"), cssVar("--series-2"), cssVar("--series-3")],
      pos: cssVar("--div-pos"), neg: cssVar("--div-neg"),
      copper: cssVar("--copper"), copperBack: cssVar("--copper-back"),
      fr4: cssVar("--fr4"), pad: cssVar("--pad"), accent: cssVar("--accent"),
      good: cssVar("--good"), critical: cssVar("--critical")
    };
  }
  var T = tokens();

  function hexRgb(hex) {
    hex = hex.replace("#", "");
    if (hex.length === 3) hex = hex.replace(/./g, function (c) { return c + c; });
    var n = parseInt(hex, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function fmt(v, sig) {
    if (v === null || v === undefined || !isFinite(v)) return "–";
    var s = Number(v).toPrecision(sig || 3);
    return String(parseFloat(s));
  }
  function el(tag, cls, parent) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (parent) parent.appendChild(e);
    return e;
  }
  var SVGNS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs, parent) {
    var e = document.createElementNS(SVGNS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function ringPath(pts) {           // data y-up -> svg y-down
    var d = "M" + pts[0][0] + " " + (-pts[0][1]);
    for (var i = 1; i < pts.length; i++) d += "L" + pts[i][0] + " " + (-pts[i][1]);
    return d + "Z";
  }
  function lerp(a, b, t) { return a + (b - a) * t; }

  /* ---------------- field grid sampling & colormap ---------------- */

  var FIELD = DATA.field;
  function sampleBz(r_mm, phi) {     // bilinear in (r, phi); tesla*1e4 = 0.1mT ints
    var nr = FIELD.nr, np = FIELD.nphi;
    var fr = (r_mm - FIELD.r0_mm) / (FIELD.r1_mm - FIELD.r0_mm) * (nr - 1);
    if (fr < 0 || fr > nr - 1) return null;
    var fp = (phi / (2 * Math.PI)) * np;
    fp = ((fp % np) + np) % np;
    var i0 = Math.floor(fr), i1 = Math.min(nr - 1, i0 + 1), ti = fr - i0;
    var j0 = Math.floor(fp), j1 = (j0 + 1) % np, tj = fp - j0;
    var bz = FIELD.bz;
    var v00 = bz[i0 * np + j0], v01 = bz[i0 * np + j1];
    var v10 = bz[i1 * np + j0], v11 = bz[i1 * np + j1];
    return lerp(lerp(v00, v01, tj), lerp(v10, v11, tj), ti) * FIELD.scale; // tesla
  }

  /* Render the polar field grid into a square canvas, rotor frame, theta=0.
   * mode "wash": transparent-midpoint overlay for the animation.
   * mode "map":  same diverging colors composited over the chart surface. */
  function buildFieldCanvas(sizePx, extent_mm, mode) {
    var c = document.createElement("canvas");
    c.width = c.height = sizePx;
    var ctx = c.getContext("2d");
    var img = ctx.createImageData(sizePx, sizePx);
    var d = img.data;
    var pr = hexRgb(T.pos), nr_ = hexRgb(T.neg), sf = hexRgb(T.surface);
    var peak = FIELD.bz_peak_T || 1e-9;
    var half = sizePx / 2;
    for (var py = 0; py < sizePx; py++) {
      for (var px = 0; px < sizePx; px++) {
        var x = (px - half) / half * extent_mm;
        var y = -((py - half) / half) * extent_mm;
        var r = Math.hypot(x, y);
        var idx = (py * sizePx + px) * 4;
        var bz = (r < FIELD.r0_mm || r > FIELD.r1_mm) ? null
          : sampleBz(r, Math.atan2(y, x));
        if (bz === null) { d[idx + 3] = 0; continue; }
        var t = Math.max(-1, Math.min(1, bz / peak));
        var col = t >= 0 ? pr : nr_;
        var a = Math.pow(Math.abs(t), 0.72);
        if (mode === "map") {
          d[idx] = Math.round(lerp(sf[0], col[0], a));
          d[idx + 1] = Math.round(lerp(sf[1], col[1], a));
          d[idx + 2] = Math.round(lerp(sf[2], col[2], a));
          d[idx + 3] = 255;
        } else {
          d[idx] = col[0]; d[idx + 1] = col[1]; d[idx + 2] = col[2];
          d[idx + 3] = Math.round(a * 165);
        }
      }
    }
    ctx.putImageData(img, 0, 0);
    return c;
  }

  /* ---------------- shared tooltip ---------------- */

  var tip = el("div", "chart-tip", document.body);
  function tipShow(x, y, html) {
    tip.innerHTML = html;
    tip.style.display = "block";
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var left = x + 14, top = y + 14;
    if (left + w > innerWidth - 8) left = x - w - 14;
    if (top + h > innerHeight - 8) top = y - h - 14;
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }
  function tipHide() { tip.style.display = "none"; }

  /* ================================================================
   * 1. THE SPINNING MOTOR  (hero canvas)
   * ================================================================ */

  var anim = { theta: 0, playing: !REDUCED, speed: 60, visible: true, raf: 0 };

  function artworkExtent() {
    var m = DATA.meta.od_mm / 2;
    DATA.artwork.pads.forEach(function (p) {
      p.pts.forEach(function (q) {
        m = Math.max(m, Math.abs(q[0]), Math.abs(q[1]));
      });
    });
    return Math.max(m, DATA.magnets.r_out_mm) * 1.05;
  }

  function phaseCurrents(thetaElec) {
    var d = DATA.torque.delta_rad, out = [];
    for (var k = 0; k < 3; k++) out.push(Math.cos(thetaElec - k * 2 * Math.PI / 3 + d));
    return out;
  }
  function torqueAt(thetaElecDeg) {
    var xs = DATA.torque.elec_deg, ys = DATA.torque.tau_comm_mNm, n = xs.length;
    var span = 360, t = ((thetaElecDeg % span) + span) % span;
    var step = span / n, i = Math.floor(t / step), f = t / step - i;
    return lerp(ys[i % n], ys[(i + 1) % n], f);
  }

  function initMotorAnim() {
    var canvas = document.getElementById("motor-anim");
    var hud = document.getElementById("anim-hud");
    var controls = document.getElementById("anim-controls");
    if (!canvas) return;
    controls.innerHTML = "";

    var extent = artworkExtent();
    var fieldTex = buildFieldCanvas(420, extent, "wash");
    var dpr = Math.min(devicePixelRatio || 1, 2);
    var cw = canvas.clientWidth || 480;
    canvas.width = cw * dpr; canvas.height = cw * dpr;
    var ctx = canvas.getContext("2d");
    var S = canvas.width / (2 * extent);     // px per mm
    var C = canvas.width / 2;

    // Prebuild copper paths (front layer per tooth) and pad paths in mm space.
    function toPath2D(pts) {
      var p = new Path2D(), q = pts[0];
      p.moveTo(q[0], -q[1]);
      for (var i = 1; i < pts.length; i++) p.lineTo(pts[i][0], -pts[i][1]);
      p.closePath();
      return p;
    }
    var teeth = {};                            // tooth -> [Path2D]
    DATA.artwork.fcu.forEach(function (poly) {
      (teeth[poly.tooth] = teeth[poly.tooth] || []).push(toPath2D(poly.pts));
    });
    var padPaths = DATA.artwork.pads.map(function (p) { return toPath2D(p.pts); });

    var show = { field: true, magnets: true };
    var p = DATA.meta.pole_pairs;
    var seriesRgb = T.series.map(hexRgb);

    function draw() {
      var w = canvas.width;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, w, w);

      // board disk + annulus guides
      ctx.setTransform(S, 0, 0, S, C, C);      // mm coords, y-down = -data-y
      ctx.beginPath();
      ctx.arc(0, 0, DATA.meta.od_mm / 2, 0, 2 * Math.PI);
      ctx.fillStyle = T.page; ctx.fill();
      ctx.strokeStyle = T.baseline; ctx.lineWidth = 0.3; ctx.stroke();

      // rotating field wash (rotor frame texture rotated by theta)
      if (show.field) {
        ctx.save();
        ctx.rotate(-anim.theta);               // data CCW = canvas negative
        ctx.drawImage(fieldTex, -extent, -extent, 2 * extent, 2 * extent);
        ctx.restore();
      }

      // copper, tinted by instantaneous phase current
      var I = phaseCurrents(anim.theta * p);
      for (var k = 0; k < DATA.meta.slots; k++) {
        var lay = DATA.layout[k] || { phase: k % 3, sign: 1 };
        var cur = I[lay.phase] * lay.sign;
        var rgb = seriesRgb[lay.phase];
        var a = 0.12 + 0.72 * Math.abs(cur);
        ctx.fillStyle = "rgba(" + rgb[0] + "," + rgb[1] + "," + rgb[2] + "," + a.toFixed(3) + ")";
        (teeth[k] || []).forEach(function (path) { ctx.fill(path); });
      }
      ctx.fillStyle = T.pad;
      padPaths.forEach(function (path) { ctx.fill(path); });

      // rotor magnets (outlines + polarity), rotating
      if (show.magnets) {
        ctx.save();
        ctx.rotate(-anim.theta);
        ctx.lineWidth = 0.35;
        DATA.magnets.items.forEach(function (mgt) {
          ctx.beginPath();
          if (mgt.kind === "circle") {
            ctx.arc(mgt.cx, -mgt.cy, mgt.r, 0, 2 * Math.PI);
          } else {
            ctx.moveTo(mgt.pts[0][0], -mgt.pts[0][1]);
            for (var i = 1; i < mgt.pts.length; i++) ctx.lineTo(mgt.pts[i][0], -mgt.pts[i][1]);
            ctx.closePath();
          }
          ctx.strokeStyle = mgt.pol > 0 ? T.pos : T.neg;
          ctx.stroke();
        });
        ctx.restore();
      }

      // torque bar (bottom) -- instantaneous vs mean
      var tauNow = torqueAt(anim.theta * p * 180 / Math.PI);
      var tauMax = Math.max.apply(null, DATA.torque.tau_comm_mNm.map(Math.abs)) * 1.15 || 1;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      var bw = w * 0.42, bx = w * 0.29, by = w - 14 * dpr, bh = 5 * dpr;
      ctx.fillStyle = T.grid;
      ctx.fillRect(bx, by, bw, bh);
      ctx.fillStyle = T.accent;
      ctx.fillRect(bx, by, bw * Math.max(0, tauNow / tauMax), bh);
      ctx.fillStyle = T.muted;
      ctx.font = 10 * dpr + "px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("torque", bx + bw / 2, by - 4 * dpr);

      // HUD
      var mech = ((anim.theta * 180 / Math.PI) % 360 + 360) % 360;
      hud.innerHTML =
        "θ " + mech.toFixed(1) + "°&thinsp;mech · τ " + fmt(tauNow, 3) + " mNm · " +
        ["A", "B", "C"].map(function (nm, i) {
          return '<span style="color:' + T.series[i] + '">●</span>' +
            (I[i] >= 0 ? "+" : "−") + Math.abs(I[i]).toFixed(2);
        }).join(" ");
    }

    var last = 0;
    function loop(ts) {
      anim.raf = requestAnimationFrame(loop);
      if (!anim.visible) { last = ts; return; }
      var dt = Math.min(0.05, (ts - last) / 1000 || 0);
      last = ts;
      if (anim.playing) {
        anim.theta += (anim.speed * Math.PI / 180) * dt / p;  // speed = elec deg/s
        scrub.value = ((anim.theta * p * 180 / Math.PI) % 360 + 360) % 360;
      }
      draw();
    }

    // ---- controls ----
    var btn = el("button", null, controls);
    btn.textContent = anim.playing ? "❚❚ pause" : "▶ play";
    btn.onclick = function () {
      anim.playing = !anim.playing;
      btn.textContent = anim.playing ? "❚❚ pause" : "▶ play";
    };
    var speedLab = el("label", null, controls);
    speedLab.appendChild(document.createTextNode("speed"));
    var speed = el("input", null, speedLab);
    speed.type = "range"; speed.min = 5; speed.max = 240; speed.value = anim.speed;
    speed.oninput = function () { anim.speed = +speed.value; };
    var scrubLab = el("label", null, controls);
    scrubLab.appendChild(document.createTextNode("angle"));
    var scrub = el("input", null, scrubLab);
    scrub.type = "range"; scrub.min = 0; scrub.max = 360; scrub.step = 0.5; scrub.value = 0;
    scrub.oninput = function () {
      anim.playing = false; btn.textContent = "▶ play";
      anim.theta = (+scrub.value) * Math.PI / 180 / p;
    };
    [["field", "field"], ["magnets", "magnets"]].forEach(function (pair) {
      var lab = el("label", null, controls);
      var cb = el("input", null, lab);
      cb.type = "checkbox"; cb.checked = true;
      cb.onchange = function () { show[pair[0]] = cb.checked; };
      lab.appendChild(document.createTextNode(pair[1]));
    });

    // drag to spin
    var dragging = null;
    canvas.addEventListener("pointerdown", function (ev) {
      canvas.setPointerCapture(ev.pointerId);
      var rc = canvas.getBoundingClientRect();
      dragging = Math.atan2(-(ev.clientY - rc.top - rc.height / 2),
        ev.clientX - rc.left - rc.width / 2) + anim.theta;
    });
    canvas.addEventListener("pointermove", function (ev) {
      if (dragging === null) return;
      var rc = canvas.getBoundingClientRect();
      var a = Math.atan2(-(ev.clientY - rc.top - rc.height / 2),
        ev.clientX - rc.left - rc.width / 2);
      anim.theta = dragging - a;
      anim.playing = false; btn.textContent = "▶ play";
    });
    canvas.addEventListener("pointerup", function () { dragging = null; });

    new IntersectionObserver(function (entries) {
      anim.visible = entries[0].isIntersecting;
    }, { threshold: 0.05 }).observe(canvas);

    cancelAnimationFrame(anim.raf);
    anim.raf = requestAnimationFrame(loop);
    draw();
  }

  /* ================================================================
   * 2. COPPER VIEWER  (zoom/pan SVG)
   * ================================================================ */

  function initCopperViewer() {
    var host = document.getElementById("copper-viewer");
    var bar = document.getElementById("viewer-toolbar");
    if (!host) return;
    host.innerHTML = ""; bar.innerHTML = "";

    var ext = artworkExtent() * 1.02;
    var vb0 = [-ext * 1.25, -ext, 2.5 * ext, 2 * ext];
    var vb = vb0.slice();
    var svg = svgEl("svg", {
      viewBox: vb.join(" "), preserveAspectRatio: "xMidYMid meet",
      role: "img", "aria-label": "interactive board copper artwork"
    }, host);

    // board outline
    var gBoard = svgEl("g", {}, svg);
    svgEl("circle", {
      cx: 0, cy: 0, r: DATA.meta.od_mm / 2, fill: T.surface,
      stroke: T.baseline, "stroke-width": 0.25
    }, gBoard);
    svgEl("path", {
      d: "M-1.2 0H1.2M0 -1.2V1.2", stroke: T.muted, "stroke-width": 0.12
    }, gBoard);

    function polyGroup(list, fill, opacity) {
      var g = svgEl("g", { fill: fill, "fill-opacity": opacity, "fill-rule": "evenodd" }, svg);
      list.forEach(function (poly) {
        svgEl("path", { d: ringPath(poly.pts) }, g);
      });
      return g;
    }
    var gB = polyGroup(DATA.artwork.bcu, T.copperBack, 0.85);
    var gF = polyGroup(DATA.artwork.fcu, T.copper, 0.92);
    var gPads = svgEl("g", { fill: T.pad }, svg);
    DATA.artwork.pads.forEach(function (p) {
      var path = svgEl("path", { d: ringPath(p.pts) }, gPads);
      var t = svgEl("title", {}, path);
      t.textContent = "pad " + p.name + " (" + p.layer + ".Cu)";
    });
    var gVias = svgEl("g", {}, svg);
    DATA.artwork.vias.forEach(function (v) {
      svgEl("circle", { cx: v[0], cy: -v[1], r: DATA.artwork.via_d / 2, fill: T.pad }, gVias);
      svgEl("circle", { cx: v[0], cy: -v[1], r: DATA.artwork.via_d / 4, fill: T.surface }, gVias);
    });
    var gMag = svgEl("g", {
      fill: "none", stroke: T.series[1], "stroke-width": 0.22, "stroke-dasharray": "0.7 0.45"
    }, svg);
    DATA.magnets.items.forEach(function (m) {
      if (m.kind === "circle") {
        svgEl("circle", { cx: m.cx, cy: -m.cy, r: m.r }, gMag);
      } else {
        svgEl("path", { d: ringPath(m.pts) }, gMag);
      }
    });

    var note = el("div", "zoom-note", host);
    function updateNote() { note.textContent = "×" + (vb0[2] / vb[2]).toFixed(1); }
    updateNote();

    // layer toggles
    var toggles = [
      ["F.Cu", gF, T.copper], ["B.Cu", gB, T.copperBack],
      ["pads + vias", null, T.pad], ["magnet rings", gMag, T.series[1]]
    ];
    toggles.forEach(function (tg) {
      var lab = el("label", null, bar);
      var cb = el("input", null, lab);
      cb.type = "checkbox"; cb.checked = true;
      var sw = el("span", "swatch", lab);
      sw.style.background = tg[2];
      lab.appendChild(document.createTextNode(tg[0]));
      var targets = tg[1] ? [tg[1]] : [gPads, gVias];
      cb.onchange = function () {
        targets.forEach(function (g) { g.style.display = cb.checked ? "" : "none"; });
      };
    });
    var hint = el("span", "muted", bar);
    hint.textContent = "Ø" + fmt(DATA.meta.od_mm, 3) + " mm board · " +
      DATA.artwork.fcu.length + " coil polys/side";

    function apply() { svg.setAttribute("viewBox", vb.join(" ")); updateNote(); }
    function clientToUser(ev) {
      var rc = host.getBoundingClientRect();
      return [vb[0] + (ev.clientX - rc.left) / rc.width * vb[2],
              vb[1] + (ev.clientY - rc.top) / rc.height * vb[3]];
    }
    host.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      var f = Math.exp(ev.deltaY * 0.0015);
      f = Math.max(0.05 / (vb0[2] / vb[2]), Math.min(f, 40 / (vb0[2] / vb[2]) ));
      var pt = clientToUser(ev);
      vb = [pt[0] - (pt[0] - vb[0]) * f, pt[1] - (pt[1] - vb[1]) * f, vb[2] * f, vb[3] * f];
      apply();
    }, { passive: false });

    var pointers = {};
    host.addEventListener("pointerdown", function (ev) {
      host.setPointerCapture(ev.pointerId);
      pointers[ev.pointerId] = [ev.clientX, ev.clientY];
    });
    host.addEventListener("pointermove", function (ev) {
      if (!(ev.pointerId in pointers)) return;
      var ids = Object.keys(pointers);
      var rc = host.getBoundingClientRect();
      if (ids.length === 1) {
        var dx = (ev.clientX - pointers[ev.pointerId][0]) / rc.width * vb[2];
        var dy = (ev.clientY - pointers[ev.pointerId][1]) / rc.height * vb[3];
        vb[0] -= dx; vb[1] -= dy;
      } else if (ids.length === 2) {
        var other = pointers[ids[0] == ev.pointerId ? ids[1] : ids[0]];
        var d0 = Math.hypot(pointers[ev.pointerId][0] - other[0],
                            pointers[ev.pointerId][1] - other[1]);
        var d1 = Math.hypot(ev.clientX - other[0], ev.clientY - other[1]);
        if (d0 > 0 && d1 > 0) {
          var f = d0 / d1;
          var cx = vb[0] + vb[2] / 2, cy = vb[1] + vb[3] / 2;
          vb = [cx - vb[2] * f / 2, cy - vb[3] * f / 2, vb[2] * f, vb[3] * f];
        }
      }
      pointers[ev.pointerId] = [ev.clientX, ev.clientY];
      apply();
    });
    function drop(ev) { delete pointers[ev.pointerId]; }
    host.addEventListener("pointerup", drop);
    host.addEventListener("pointercancel", drop);
    host.addEventListener("dblclick", function () { vb = vb0.slice(); apply(); });
  }

  /* ================================================================
   * 3a. WINDING RING (star-of-slots)
   * ================================================================ */

  function initWindingRing() {
    var host = document.getElementById("winding-ring");
    if (!host) return;
    host.innerHTML = "";
    var svg = svgEl("svg", { viewBox: "-110 -110 220 220" }, host);
    var slots = DATA.meta.slots, poles = DATA.meta.poles;
    var names = ["A", "B", "C"];

    function wedge(r0, r1, a0, a1) {
      var large = (a1 - a0) > Math.PI ? 1 : 0;
      function pt(r, a) { return r * Math.cos(a) + " " + (-r * Math.sin(a)); }
      return "M" + pt(r0, a0) + "A" + r0 + " " + r0 + " 0 " + large + " 0 " + pt(r0, a1) +
        "L" + pt(r1, a1) + "A" + r1 + " " + r1 + " 0 " + large + " 1 " + pt(r1, a0) + "Z";
    }
    // teeth (phase identity = categorical slots, fixed order A,B,C)
    var sec = 2 * Math.PI / slots, gap = sec * 0.06;
    for (var k = 0; k < slots; k++) {
      var lay = DATA.layout[k] || { phase: k % 3, sign: 1 };
      var a0 = k * sec - sec / 2 + gap, a1 = k * sec + sec / 2 - gap;
      svgEl("path", {
        d: wedge(52, 84, a0, a1), fill: T.series[lay.phase],
        "fill-opacity": lay.sign > 0 ? 0.95 : 0.45,
        stroke: T.surface, "stroke-width": 1
      }, svg);
      var am = k * sec, rl = 68;
      var tx = svgEl("text", {
        x: rl * Math.cos(am), y: -rl * Math.sin(am) + 3,
        "text-anchor": "middle", "font-size": slots > 24 ? 6.5 : 9,
        fill: T.ink, "font-weight": 600, "font-family": "system-ui, sans-serif"
      }, svg);
      tx.textContent = names[lay.phase] + (lay.sign > 0 ? "+" : "−");
    }
    // pole ring (polarity = diverging pair, matching the field wash)
    var psec = 2 * Math.PI / poles;
    for (var q = 0; q < poles; q++) {
      var b0 = q * psec + psec * 0.08, b1 = (q + 1) * psec - psec * 0.08;
      svgEl("path", {
        d: wedge(90, 101, b0, b1), fill: q % 2 === 0 ? T.pos : T.neg,
        "fill-opacity": 0.8
      }, svg);
    }
    var lbl = svgEl("text", {
      x: 0, y: 4, "text-anchor": "middle", "font-size": 12, fill: T.ink2,
      "font-family": "system-ui, sans-serif"
    }, svg);
    lbl.textContent = slots + "N" + poles + "P";
    // legend chips (phases; identity never color-alone: wedges carry labels too)
    var leg = el("div", "caption", host);
    leg.innerHTML = names.map(function (nm, i) {
      return '<span style="color:' + T.series[i] + '">■</span> phase ' + nm;
    }).join(" &nbsp; ") +
      ' &nbsp; <span style="color:' + T.pos + '">■</span> N pole' +
      ' &nbsp; <span style="color:' + T.neg + '">■</span> S pole' +
      " &nbsp; (pale = reverse-wound)";
  }

  /* ================================================================
   * 3b. EXPLODED STACK
   * ================================================================ */

  var stackState = { e: 0, autoDone: false };

  function initStack() {
    var host = document.getElementById("stack-view");
    var slider = document.getElementById("explode");
    if (!host) return;
    host.innerHTML = "";

    var items = DATA.stack.items;
    var maxOd = Math.max.apply(null, items.map(function (i) { return i.od; }));
    var W = 660, R = 172;                        // px half-width of the biggest disk
    var pxmm = R / (maxOd / 2);
    var K = 0.30;                                // ellipse squash
    var zScale = pxmm * Math.max(1, 14 / DATA.stack.total_mm); // exaggerate thin stacks
    var SEP = 46;                                // px extra separation at explode=1

    var svg = svgEl("svg", { viewBox: "0 0 " + W + " 10" }, host);

    function layout(e) {
      // y positions, top to bottom; gaps get e-scaled separation
      var y = 30, rows = [];
      items.forEach(function (it) {
        var h = Math.max(it.t * zScale, it.kind === "gap" ? 6 : 8);
        rows.push({ it: it, yTop: y, h: h });
        y += h + e * SEP + 4;
      });
      return { rows: rows, height: y + R * K + 24 };
    }
    var maxH = layout(1).height;
    svg.setAttribute("viewBox", "0 0 " + W + " " + maxH);

    var gAxis = svgEl("g", {}, svg);
    var g = svgEl("g", {}, svg);

    function diskPath(cx, yTop, h, r) {
      var ry = r * K;
      return "M" + (cx - r) + " " + yTop + " L" + (cx - r) + " " + (yTop + h) +
        " A" + r + " " + ry + " 0 0 0 " + (cx + r) + " " + (yTop + h) +
        " L" + (cx + r) + " " + yTop;
    }

    function render(e) {
      g.innerHTML = ""; gAxis.innerHTML = "";
      var L = layout(e);
      var cx = R + 34;
      // dashed axis through the middle
      svgEl("line", {
        x1: cx, y1: 12, x2: cx, y2: L.height - 8,
        stroke: T.muted, "stroke-width": 1, "stroke-dasharray": "5 5", opacity: 0.6
      }, gAxis);

      L.rows.forEach(function (row) {
        var it = row.it, r = it.od / 2 * pxmm, yTop = row.yTop, h = row.h;
        var yMid = yTop + h / 2;
        if (it.kind === "gap") {
          // dimension bracket for the air gap (fades in as the stack explodes)
          var gvis = Math.max(0, Math.min(1, (e - 0.12) * 2.2));
          svgEl("line", {
            x1: cx - r * 0.55, y1: yMid, x2: cx + r * 0.55, y2: yMid,
            stroke: T.muted, "stroke-width": 1, "stroke-dasharray": "2.5 3.5",
            opacity: 0.9 * gvis
          }, g);
          var gt = svgEl("text", {
            x: cx + r * 0.58, y: yMid + 3.5, "font-size": 11, fill: T.muted,
            "font-family": "system-ui, sans-serif", opacity: gvis
          }, g);
          gt.textContent = it.label;
          return;
        }
        var body = it.kind === "board" ? T.fr4 : T.baseline;
        svgEl("path", {
          d: diskPath(cx, yTop, h, r), fill: body, stroke: T.border,
          "stroke-width": 0.5, "fill-opacity": 0.92
        }, g);
        svgEl("ellipse", {
          cx: cx, cy: yTop, rx: r, ry: r * K,
          fill: it.kind === "board" ? T.fr4 : T.surface,
          stroke: T.border, "stroke-width": 0.5
        }, g);
        if (it.kind === "board" && it.copper) {
          var co = it.copper[1] * pxmm, ci = it.copper[0] * pxmm;
          svgEl("path", {
            d: "M" + (cx - co) + " " + yTop +
              "A" + co + " " + co * K + " 0 1 0 " + (cx + co) + " " + yTop +
              "A" + co + " " + co * K + " 0 1 0 " + (cx - co) + " " + yTop + "Z" +
              "M" + (cx - ci) + " " + yTop +
              "A" + ci + " " + ci * K + " 0 1 0 " + (cx + ci) + " " + yTop +
              "A" + ci + " " + ci * K + " 0 1 0 " + (cx - ci) + " " + yTop + "Z",
            fill: T.copper, "fill-rule": "evenodd", "fill-opacity": 0.95
          }, g);
        }
        if (it.kind === "rotor" && it.mag) {
          var items2 = DATA.magnets.items.slice()
            .sort(function (a, b) { return magY(a) - magY(b); });
          function magY(m) {
            var y0 = m.kind === "circle" ? m.cy : m.pts[0][1];
            return -y0;
          }
          items2.forEach(function (m) {
            var mx, my, mr;
            if (m.kind === "circle") { mx = m.cx; my = m.cy; mr = m.r; }
            else {
              var xs = m.pts.map(function (q) { return q[0]; });
              var ys = m.pts.map(function (q) { return q[1]; });
              mx = xs.reduce(function (a, b) { return a + b; }) / xs.length;
              my = ys.reduce(function (a, b) { return a + b; }) / ys.length;
              mr = (it.mag[1] - it.mag[0]) / 2 * 0.7;
            }
            svgEl("ellipse", {
              cx: cx + mx * pxmm, cy: yTop - my * pxmm * K,
              rx: mr * pxmm, ry: mr * pxmm * K,
              fill: m.pol > 0 ? T.pos : T.neg, "fill-opacity": 0.85,
              stroke: T.surface, "stroke-width": 0.4
            }, g);
          });
        }
        // label + leader
        var lx = cx + r + 14;
        svgEl("line", {
          x1: cx + r * 0.99, y1: yMid, x2: lx - 4, y2: yMid,
          stroke: T.muted, "stroke-width": 1
        }, g);
        var t1 = svgEl("text", {
          x: lx, y: yMid, "font-size": 12.5, fill: T.ink, "font-weight": 600,
          "font-family": "system-ui, sans-serif"
        }, g);
        t1.textContent = it.label;
        var t2 = svgEl("text", {
          x: lx, y: yMid + 14, "font-size": 10.5, fill: T.muted,
          "font-family": "system-ui, sans-serif"
        }, g);
        t2.textContent = it.note || "";
      });
    }

    render(stackState.e);
    if (slider) {
      slider.value = stackState.e;
      slider.oninput = function () { stackState.e = +slider.value; render(stackState.e); };
    }
    // auto-explode once when scrolled into view
    if (!stackState.autoDone && !REDUCED) {
      new IntersectionObserver(function (entries, obs) {
        if (!entries[0].isIntersecting || stackState.autoDone) return;
        stackState.autoDone = true;
        obs.disconnect();
        var t0 = performance.now();
        (function step(ts) {
          var f = Math.min(1, (ts - t0) / 1100);
          stackState.e = f * f * (3 - 2 * f);
          if (slider) slider.value = stackState.e;
          render(stackState.e);
          if (f < 1) requestAnimationFrame(step);
        })(t0);
      }, { threshold: 0.45 }).observe(host);
    } else {
      render(stackState.e);
    }
  }

  /* ================================================================
   * 4. CHARTS (hand-rolled SVG, hover crosshair + tooltip)
   * ================================================================ */

  function niceTicks(lo, hi, n) {
    var span = hi - lo || 1;
    var step = Math.pow(10, Math.floor(Math.log10(span / n)));
    var err = span / n / step;
    step *= err >= 7.5 ? 10 : err >= 3 ? 5 : err >= 1.5 ? 2 : 1;
    var t0 = Math.ceil(lo / step) * step, out = [];
    for (var v = t0; v <= hi + 1e-12; v += step) out.push(+v.toPrecision(12));
    return out;
  }

  /* cfg: {host, title, sub, xs, series:[{name,color,ys,dash}], xUnit, yUnit,
   *       markX, xTickFmt} */
  function lineChart(cfg) {
    var card = el("div", "chart", cfg.host);
    if (cfg.title) { el("h4", null, card).textContent = cfg.title; }
    if (cfg.sub) { el("p", "sub", card).textContent = cfg.sub; }
    if (cfg.series.length > 1) {
      var leg = el("p", "sub", card);
      leg.innerHTML = cfg.series.map(function (s) {
        return '<span style="color:' + s.color + '">' + (s.dash ? "╌" : "—") +
          "</span> " + s.name;
      }).join(" &nbsp; ");
    }
    var W = 340, H = 190, m = { l: 46, r: 12, t: 8, b: 26 };
    var svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H }, card);

    var xs = cfg.xs;
    var xlo = Math.min.apply(null, xs), xhi = Math.max.apply(null, xs);
    var ylo = Infinity, yhi = -Infinity;
    cfg.series.forEach(function (s) {
      s.ys.forEach(function (v) {
        if (v === null || !isFinite(v)) return;
        if (v < ylo) ylo = v; if (v > yhi) yhi = v;
      });
    });
    if (!(isFinite(ylo) && isFinite(yhi))) { ylo = 0; yhi = 1; }
    if (ylo > 0 && ylo < 0.35 * yhi) ylo = 0;      // include a meaningful baseline
    var pad = (yhi - ylo) * 0.08 || 1;
    yhi += pad; if (ylo !== 0) ylo -= pad;

    function X(v) { return m.l + (v - xlo) / (xhi - xlo || 1) * (W - m.l - m.r); }
    function Y(v) { return H - m.b - (v - ylo) / (yhi - ylo || 1) * (H - m.t - m.b); }

    // recessive grid + ticks
    niceTicks(ylo, yhi, 4).forEach(function (tv) {
      svgEl("line", {
        x1: m.l, x2: W - m.r, y1: Y(tv), y2: Y(tv), stroke: T.grid, "stroke-width": 1
      }, svg);
      var t = svgEl("text", {
        x: m.l - 6, y: Y(tv) + 3, "text-anchor": "end", "font-size": 9.5,
        fill: T.muted, "font-family": "system-ui, sans-serif"
      }, svg);
      t.textContent = fmt(tv, 3);
    });
    niceTicks(xlo, xhi, 5).forEach(function (tv) {
      var t = svgEl("text", {
        x: X(tv), y: H - m.b + 14, "text-anchor": "middle", "font-size": 9.5,
        fill: T.muted, "font-family": "system-ui, sans-serif"
      }, svg);
      t.textContent = cfg.xTickFmt ? cfg.xTickFmt(tv) : fmt(tv, 3);
    });
    svgEl("line", {
      x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, stroke: T.baseline, "stroke-width": 1
    }, svg);
    // axis unit labels (text tokens, never series color)
    var yl = svgEl("text", {
      x: m.l, y: m.t + 2, "font-size": 9.5, fill: T.muted,
      "font-family": "system-ui, sans-serif"
    }, svg);
    yl.textContent = cfg.yUnit || "";
    if (cfg.xUnit) {
      var xl = svgEl("text", {
        x: W - m.r, y: H - 1, "text-anchor": "end", "font-size": 9.5,
        fill: T.muted, "font-family": "system-ui, sans-serif"
      }, svg);
      xl.textContent = cfg.xUnit;
    }

    // the picked-design marker
    if (cfg.markX !== undefined && cfg.markX !== null) {
      svgEl("line", {
        x1: X(cfg.markX), x2: X(cfg.markX), y1: m.t, y2: H - m.b,
        stroke: T.muted, "stroke-width": 1, "stroke-dasharray": "3 3"
      }, svg);
      var mt = svgEl("text", {
        x: X(cfg.markX) + 3, y: H - m.b - 5, "font-size": 8.5, fill: T.muted,
        "font-family": "system-ui, sans-serif"
      }, svg);
      mt.textContent = "chosen";
    }

    cfg.series.forEach(function (s) {
      var d = "";
      xs.forEach(function (x, i) {
        var v = s.ys[i];
        if (v === null || !isFinite(v)) return;
        d += (d ? "L" : "M") + X(x).toFixed(1) + " " + Y(v).toFixed(1);
      });
      svgEl("path", {
        d: d, fill: "none", stroke: s.color, "stroke-width": 2,
        "stroke-linejoin": "round", "stroke-linecap": "round",
        "stroke-dasharray": s.dash ? "5 4" : "none"
      }, svg);
      if (cfg.dots) {
        xs.forEach(function (x, i) {
          if (s.ys[i] === null || !isFinite(s.ys[i])) return;
          svgEl("circle", {
            cx: X(x), cy: Y(s.ys[i]), r: 3.5, fill: s.color,
            stroke: T.surface, "stroke-width": 1.5
          }, svg);
        });
      }
    });

    // hover layer: nearest-x crosshair + shared tooltip
    var cross = svgEl("line", {
      x1: 0, x2: 0, y1: m.t, y2: H - m.b, stroke: T.muted, "stroke-width": 1,
      opacity: 0
    }, svg);
    var hoverDots = cfg.series.map(function (s) {
      return svgEl("circle", {
        r: 4, fill: s.color, stroke: T.surface, "stroke-width": 1.5, opacity: 0
      }, svg);
    });
    var hot = svgEl("rect", {
      x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b,
      fill: "transparent"
    }, svg);
    hot.addEventListener("pointermove", function (ev) {
      var rc = svg.getBoundingClientRect();
      var ux = xlo + ((ev.clientX - rc.left) / rc.width * W - m.l) /
        (W - m.l - m.r) * (xhi - xlo);
      var best = 0, bd = Infinity;
      xs.forEach(function (x, i) {
        var d = Math.abs(x - ux);
        if (d < bd) { bd = d; best = i; }
      });
      cross.setAttribute("x1", X(xs[best]));
      cross.setAttribute("x2", X(xs[best]));
      cross.setAttribute("opacity", 0.7);
      var rows = "";
      cfg.series.forEach(function (s, si) {
        var v = s.ys[best];
        hoverDots[si].setAttribute("opacity", v === null || !isFinite(v) ? 0 : 1);
        if (v !== null && isFinite(v)) {
          hoverDots[si].setAttribute("cx", X(xs[best]));
          hoverDots[si].setAttribute("cy", Y(v));
        }
        rows += '<div class="t-row"><span class="t-swatch" style="background:' +
          s.color + '"></span>' + (s.name || cfg.title) + ": <b>" + fmt(v, 4) +
          (cfg.yUnit ? " " + cfg.yUnit : "") + "</b></div>";
      });
      tipShow(ev.clientX, ev.clientY,
        '<div class="t-title">' + (cfg.xTickFmt ? cfg.xTickFmt(xs[best]) : fmt(xs[best], 4)) +
        (cfg.xUnit ? " " + cfg.xUnit : "") + "</div>" + rows);
    });
    hot.addEventListener("pointerleave", function () {
      cross.setAttribute("opacity", 0);
      hoverDots.forEach(function (d) { d.setAttribute("opacity", 0); });
      tipHide();
    });
    return card;
  }

  function initSweepCharts() {
    var host = document.getElementById("sweep-charts");
    if (!host || !DATA.sweep) return;
    host.innerHTML = "";
    var pts = DATA.sweep.points;
    var xs = pts.map(function (p) { return p.x; });
    function ys(key) { return pts.map(function (p) { return p[key]; }); }
    var mark = DATA.sweep.picked_x;
    var xu = DATA.sweep.unit, xl2 = DATA.sweep.label.toLowerCase() + " (" + xu + ")";
    lineChart({
      host: host, title: "Continuous torque", sub: "what you keep — " + xl2,
      xs: xs, xUnit: xu, yUnit: "mNm", markX: mark, dots: true,
      series: [{ name: "τ_cont", color: T.series[0], ys: ys("tau_cont_mNm") }]
    });
    lineChart({
      host: host, title: "Phase inductance", sub: "what fights the ripple — air-core L, series total",
      xs: xs, xUnit: xu, yUnit: "µH", markX: mark, dots: true,
      series: [{ name: "L_phase", color: T.series[0], ys: ys("l_phase_uH") }]
    });
    lineChart({
      host: host, title: "PWM ripple, bare winding", sub: "worst-case peak-to-peak at the reference drive",
      xs: xs, xUnit: xu, yUnit: "A pp", markX: mark, dots: true,
      series: [{ name: "ripple", color: T.series[0], ys: ys("pwm_ripple_A_pp") }]
    });
    lineChart({
      host: host, title: "External choke needed", sub: "per phase, to hit the ripple budget",
      xs: xs, xUnit: xu, yUnit: "µH", markX: mark, dots: true,
      series: [{ name: "L_ext", color: T.series[0], ys: ys("l_ext_uH") }]
    });
  }

  function initTorqueChart() {
    var host = document.getElementById("torque-chart");
    if (!host) return;
    host.innerHTML = "";
    var tq = DATA.torque;
    lineChart({
      host: host, title: "Torque vs rotor angle",
      sub: "one electrical period at " + fmt(tq.i_amp, 3) + " A",
      xs: tq.elec_deg, xUnit: "° elec", yUnit: "mNm",
      xTickFmt: function (v) { return Math.round(v) + "°"; },
      series: [
        { name: "commutated", color: T.series[0], ys: tq.tau_comm_mNm },
        { name: "frozen-current characteristic", color: T.series[1], ys: tq.tau_dc_mNm, dash: true }
      ]
    });
  }

  function initBzProfile() {
    var host = document.getElementById("bz-profile");
    if (!host) return;
    host.innerHTML = "";
    var rMean = (DATA.coil_annulus_mm[0] + DATA.coil_annulus_mm[1]) / 2;
    var np2 = FIELD.nphi, xs = [], ys = [];
    var ri = Math.round((rMean - FIELD.r0_mm) / (FIELD.r1_mm - FIELD.r0_mm) * (FIELD.nr - 1));
    ri = Math.max(0, Math.min(FIELD.nr - 1, ri));
    for (var j = 0; j < np2; j++) {
      xs.push(j / np2 * 360);
      ys.push(FIELD.bz[ri * np2 + j] * FIELD.scale * 1000);   // mT
    }
    xs.push(360); ys.push(ys[0]);
    lineChart({
      host: host, title: "Airgap Bz around the ring",
      sub: "at r = " + fmt(rMean, 3) + " mm (mean coil radius), rotor at 0°",
      xs: xs, xUnit: "° mech", yUnit: "mT",
      xTickFmt: function (v) { return Math.round(v) + "°"; },
      series: [{ name: "Bz", color: T.series[0], ys: ys }]
    });
  }

  /* ================================================================
   * 5. FIELD MAP (canvas heatmap + hover values)
   * ================================================================ */

  function initFieldMap() {
    var canvas = document.getElementById("field-map");
    if (!canvas) return;
    var ext = FIELD.r1_mm * 1.06;
    var dpr = Math.min(devicePixelRatio || 1, 2);
    var cw = canvas.clientWidth || 420;
    canvas.width = cw * dpr; canvas.height = cw * dpr;
    var ctx = canvas.getContext("2d");
    var texture = buildFieldCanvas(Math.min(640, cw * dpr), ext, "map");
    // background
    ctx.fillStyle = T.surface;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(texture, 0, 0, canvas.width, canvas.height);
    // coil annulus outline
    var S = canvas.width / (2 * ext), C = canvas.width / 2;
    ctx.strokeStyle = T.ink2; ctx.lineWidth = 1 * dpr; ctx.setLineDash([4 * dpr, 4 * dpr]);
    [DATA.coil_annulus_mm[0], DATA.coil_annulus_mm[1]].forEach(function (r) {
      ctx.beginPath(); ctx.arc(C, C, r * S, 0, 2 * Math.PI); ctx.stroke();
    });
    ctx.setLineDash([]);
    // diverging legend bar (blue -> neutral -> red), labelled in text tokens
    var lw = canvas.width * 0.3, lx = canvas.width * 0.035, ly = canvas.height - 18 * dpr;
    var grad = ctx.createLinearGradient(lx, 0, lx + lw, 0);
    grad.addColorStop(0, T.neg); grad.addColorStop(0.5, T.surface); grad.addColorStop(1, T.pos);
    ctx.fillStyle = grad;
    ctx.fillRect(lx, ly, lw, 6 * dpr);
    ctx.fillStyle = T.muted;
    ctx.font = 9 * dpr + "px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText("−" + fmt(FIELD.bz_peak_T, 2) + " T", lx, ly - 3 * dpr);
    ctx.textAlign = "right";
    ctx.fillText("+" + fmt(FIELD.bz_peak_T, 2) + " T", lx + lw, ly - 3 * dpr);

    canvas.onpointermove = function (ev) {
      var rc = canvas.getBoundingClientRect();
      var x = ((ev.clientX - rc.left) / rc.width * 2 - 1) * ext;
      var y = -((ev.clientY - rc.top) / rc.height * 2 - 1) * ext;
      var r = Math.hypot(x, y);
      var bz = (r < FIELD.r0_mm || r > FIELD.r1_mm) ? null : sampleBz(r, Math.atan2(y, x));
      if (bz === null) { tipHide(); return; }
      tipShow(ev.clientX, ev.clientY,
        '<div class="t-title">Bz = ' + fmt(bz * 1000, 3) + " mT</div>" +
        '<div class="t-row">r = ' + fmt(r, 3) + " mm, φ = " +
        fmt(((Math.atan2(y, x) * 180 / Math.PI) + 360) % 360, 3) + "°</div>");
    };
    canvas.onpointerleave = tipHide;
  }

  /* ================================================================
   * boot, reveal, theme & resize handling
   * ================================================================ */

  function initReveal() {
    if (REDUCED) return;
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("shown"); obs.unobserve(en.target); }
      });
    }, { threshold: 0.12 });
    document.querySelectorAll("main section").forEach(function (s) {
      s.classList.add("reveal");
      obs.observe(s);
    });
  }

  function buildAll() {
    T = tokens();
    initMotorAnim();
    initCopperViewer();
    initWindingRing();
    initStack();
    initSweepCharts();
    initTorqueChart();
    initBzProfile();
    initFieldMap();
  }

  buildAll();
  initReveal();

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", buildAll);
  var rsTimer = 0;
  addEventListener("resize", function () {
    clearTimeout(rsTimer);
    rsTimer = setTimeout(function () {
      initMotorAnim(); initFieldMap();
    }, 250);
  });
})();
