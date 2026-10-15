// PaisaMaps business site: carousels, tabs, before/after slider, menus. No dependencies.
(function () {
  "use strict";

  // Mobile drawer
  var toggle = document.querySelector(".nav-toggle"), drawer = document.getElementById("drawer");
  if (toggle && drawer) {
    toggle.addEventListener("click", function () {
      var open = drawer.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      document.body.style.overflow = open ? "hidden" : "";
    });
  }

  // Desktop mega menus
  var megaBtns = document.querySelectorAll("[data-mega]");
  function closeAll(except) {
    megaBtns.forEach(function (b) {
      if (b === except) return;
      b.setAttribute("aria-expanded", "false");
      var m = document.getElementById(b.getAttribute("data-mega"));
      if (m) m.classList.remove("open");
    });
  }
  megaBtns.forEach(function (b) {
    b.addEventListener("click", function (e) {
      e.stopPropagation();
      var m = document.getElementById(b.getAttribute("data-mega"));
      var open = !m.classList.contains("open");
      closeAll(b);
      m.classList.toggle("open", open);
      b.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });
  document.addEventListener("click", function (e) { if (!e.target.closest(".mega")) closeAll(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeAll(); });

  // Carousels: scroll-snap track + prev/next + dots + caption; autoplay only for [data-autoplay]
  document.querySelectorAll(".carousel").forEach(function (car) {
    var track = car.querySelector(".car-track");
    var slides = Array.prototype.slice.call(track.children);
    var dots = car.querySelector(".dots");
    var cap = car.querySelector("[data-cap]");
    var idx = 0, timer = null;
    var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    function perView() { return Math.max(1, Math.round(track.clientWidth / (slides[0].getBoundingClientRect().width || 1))); }
    function go(i) {
      var max = Math.max(0, slides.length - perView());
      idx = (i + max + 1) % (max + 1);
      track.scrollTo({ left: slides[idx].offsetLeft - track.offsetLeft, behavior: reduce ? "auto" : "smooth" });
    }
    if (dots) {
      slides.forEach(function (s, i) {
        var d = document.createElement("button");
        d.type = "button"; d.setAttribute("aria-label", "Show slide " + (i + 1));
        d.addEventListener("click", function () { go(i); stop(); });
        dots.appendChild(d);
      });
    }
    function mark() {
      var w = slides[0].getBoundingClientRect().width + 12;
      idx = Math.round(track.scrollLeft / w);
      if (dots) Array.prototype.forEach.call(dots.children, function (d, i) { d.setAttribute("aria-current", i === idx ? "true" : "false"); });
      if (cap && slides[idx]) cap.innerHTML = slides[idx].getAttribute("data-caption") || "";
    }
    track.addEventListener("scroll", function () { window.requestAnimationFrame(mark); }, { passive: true });
    var prev = car.querySelector("[data-prev]"), next = car.querySelector("[data-next]");
    if (prev) prev.addEventListener("click", function () { go(idx - 1); stop(); });
    if (next) next.addEventListener("click", function () { go(idx + 1); stop(); });
    function stop() { if (timer) { clearInterval(timer); timer = null; } }
    if (car.hasAttribute("data-autoplay") && !reduce) {
      timer = setInterval(function () { go(idx + 1); }, 5000);
      car.addEventListener("pointerdown", stop);
      car.addEventListener("focusin", stop);
    }
    mark();
  });

  // Tabs (audience switcher)
  document.querySelectorAll("[data-tabs]").forEach(function (root) {
    var btns = root.querySelectorAll("[role=tab]");
    function select(b) {
      btns.forEach(function (x) {
        var on = x === b;
        x.setAttribute("aria-selected", on ? "true" : "false");
        x.tabIndex = on ? 0 : -1;
        var p = document.getElementById(x.getAttribute("aria-controls"));
        if (p) p.hidden = !on;
      });
    }
    btns.forEach(function (b, i) {
      b.addEventListener("click", function () { select(b); });
      b.addEventListener("keydown", function (e) {
        var n = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (!n) return;
        var t = btns[(i + n + btns.length) % btns.length]; t.focus(); select(t);
      });
    });
  });

  // Before/after slider
  document.querySelectorAll(".compare").forEach(function (c) {
    var input = c.querySelector("input");
    function set() { c.style.setProperty("--pos", input.value + "%"); }
    input.addEventListener("input", set); set();
  });
})();
