/* Illustrated lilies, petals, diyas — small SVG string generators.
   Shared gradients live in <svg id="defs"> in index.html. */
(function () {
  "use strict";

  // One lily petal pointing up from the centre (50,52). Outer petals are a
  // touch wider and longer; the tip curls slightly to one side.
  var OUTER = "M50 52 C 37 41, 34 22, 48 5 C 50 3, 52 3, 53 6 C 64 22, 62 41, 50 52 Z";
  var INNER = "M50 52 C 40 41, 38 24, 49 10 C 50 8, 52 8, 53 11 C 61 24, 59 41, 50 52 Z";

  function flower(opts) {
    opts = opts || {};
    var cls = "lily" + (opts.bloom ? " lily--bloom" : "") + (opts.className ? " " + opts.className : "");
    var p = [];
    var i, a;
    // outer three petals (0, 120, 240) then inner three (60, 180, 300)
    for (i = 0; i < 3; i++) {
      a = i * 120;
      p.push('<g transform="rotate(' + a + ' 50 52)"><path class="petal" style="--i:' + i + '" d="' + OUTER + '" fill="url(#lilyPetal)" stroke="url(#lilyEdge)" stroke-width=".6"/>' +
        '<path class="petal vein" style="--i:' + i + '" d="M50 48 C 49 34, 49 22, 50 12" stroke="#e8b7b9" stroke-width=".8" fill="none" opacity=".7"/>' +
        '<g class="petal" style="--i:' + i + '" fill="#d4646c" opacity=".55"><circle cx="47" cy="36" r=".9"/><circle cx="53" cy="33" r=".8"/><circle cx="49.5" cy="29" r=".7"/><circle cx="52" cy="40" r=".7"/></g></g>');
    }
    for (i = 0; i < 3; i++) {
      a = 60 + i * 120;
      p.push('<g transform="rotate(' + a + ' 50 52)"><path class="petal" style="--i:' + (i + 3) + '" d="' + INNER + '" fill="url(#lilyPetal)" stroke="url(#lilyEdge)" stroke-width=".5"/>' +
        '<path class="petal vein" style="--i:' + (i + 3) + '" d="M50 48 C 49 36, 49 26, 50 15" stroke="#e8b7b9" stroke-width=".7" fill="none" opacity=".7"/></g>');
    }
    // stamens
    var st = '<g class="stamens">';
    for (i = 0; i < 6; i++) {
      a = 30 + i * 60;
      st += '<g transform="rotate(' + a + ' 50 52)"><path d="M50 52 Q 51 44 50 36" stroke="#c9d7a6" stroke-width=".9" fill="none"/>' +
        '<ellipse cx="50" cy="35" rx="1.1" ry="2.6" fill="#b8434c"/></g>';
    }
    st += '<circle cx="50" cy="52" r="2.6" fill="#cfe0b2"/><circle cx="50" cy="52" r="1.2" fill="#9fbf7a"/></g>';
    return '<svg class="' + cls + '" viewBox="0 0 100 104" aria-hidden="true" focusable="false">' +
      '<circle cx="50" cy="52" r="34" fill="url(#lilyGlow)" class="glow"/>' + p.join("") + st + "</svg>";
  }

  // A lily on a stem with leaves and a bud — for corners and headers.
  function sprig(opts) {
    opts = opts || {};
    var cls = "sprig" + (opts.className ? " " + opts.className : "");
    return '<svg class="' + cls + '" viewBox="0 0 160 240" aria-hidden="true" focusable="false">' +
      // stems
      '<path d="M78 238 C 80 190, 70 150, 60 96" stroke="#9db88a" stroke-width="2.4" fill="none" stroke-linecap="round"/>' +
      '<path d="M76 180 C 92 150, 112 128, 122 92" stroke="#9db88a" stroke-width="1.8" fill="none" stroke-linecap="round"/>' +
      // leaves
      '<path d="M77 200 C 52 188, 38 168, 30 146 C 52 158, 68 172, 77 200 Z" fill="url(#leaf)"/>' +
      '<path d="M79 214 C 100 204, 118 188, 128 166 C 106 176, 90 190, 79 214 Z" fill="url(#leaf)"/>' +
      '<path d="M70 150 C 54 140, 46 126, 44 110 C 58 120, 66 132, 70 150 Z" fill="url(#leaf)" opacity=".85"/>' +
      // bud
      '<g transform="translate(122 94) rotate(14)"><path d="M0 0 C -7 -10, -6 -30, 0 -42 C 6 -30, 7 -10, 0 0 Z" fill="url(#bud)" stroke="#e7d9c4" stroke-width=".6"/>' +
      '<path d="M0 -2 C -1 -14, -1 -28, 0 -40" stroke="#eab9ba" stroke-width=".7" fill="none"/></g>' +
      // flower
      '<g transform="translate(14 22) scale(.92) rotate(-12 50 52)">' + flower({}).replace(/^<svg[^>]*>/, "").replace(/<\/svg>$/, "") + "</g>" +
      "</svg>";
  }

  // A single loose petal (for drifting / falling).
  function petal() {
    return '<svg viewBox="0 0 20 34" aria-hidden="true" focusable="false"><path d="M10 33 C 2 25, 1 11, 9 1 C 10 0, 11 0, 12 1 C 19 11, 18 25, 10 33 Z" fill="url(#lilyPetal2)" stroke="#ecd9d3" stroke-width=".5"/><path d="M10 30 C 10 22, 10 12, 10.5 4" stroke="#efc6c6" stroke-width=".6" fill="none"/></svg>';
  }

  // A clay diya with a flickering flame.
  function diya() {
    return '<svg class="diya" viewBox="0 0 80 64" aria-hidden="true" focusable="false">' +
      '<ellipse cx="40" cy="58" rx="26" ry="3" fill="#000" opacity=".06"/>' +
      '<g class="flame"><path d="M40 4 C 46 14, 48 22, 40 30 C 32 22, 34 14, 40 4 Z" fill="url(#flame)"/>' +
      '<path d="M40 14 C 43 19, 43 24, 40 28 C 37 24, 37 19, 40 14 Z" fill="#fff6d8"/></g>' +
      '<path d="M8 32 C 12 52, 68 52, 72 32 C 60 38, 20 38, 8 32 Z" fill="url(#clay)"/>' +
      '<path d="M8 32 C 20 38, 60 38, 72 32 C 64 30, 16 30, 8 32 Z" fill="#e7a07a"/>' +
      '<path d="M16 40 Q 40 48 64 40" stroke="#f6e1b8" stroke-width="1.4" fill="none" stroke-dasharray="1 3.5" stroke-linecap="round"/>' +
      "</svg>";
  }

  window.Lily = { flower: flower, sprig: sprig, petal: petal, diya: diya };
})();
