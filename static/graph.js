// Obsidian-style graph of posts: a small force layout with drag, hover highlighting and
// keyboard-reachable links. No dependencies; the data comes from build.py.
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  function el(tag, attrs = {}) {
    const node = document.createElementNS(NS, tag);
    for (const key in attrs) node.setAttribute(key, attrs[key]);
    return node;
  }

  for (const figure of document.querySelectorAll("figure.graph")) {
    const data = JSON.parse(figure.querySelector('script[type="application/json"]').textContent);
    if (data.nodes.length > 1) draw(figure, data);
  }

  function draw(figure, { nodes, links }) {
    const root = figure.querySelector(".graph-root").href; // site root, whatever folder the site lives in
    const height = parseFloat(getComputedStyle(figure).getPropertyValue("--graph-height")) || 360;
    let width = 0;

    const svg = el("svg", { class: "graph-svg" });
    const edgeLayer = el("g"), nodeLayer = el("g");
    svg.append(edgeLayer, nodeLayer);
    figure.append(svg);
    figure.classList.add("ready");

    const neighbours = nodes.map(() => new Set());
    for (const [a, b] of links) { neighbours[a].add(b); neighbours[b].add(a); }

    nodes.forEach((n, i) => {
      const angle = i * Math.PI * (3 - Math.sqrt(5)), r = 14 * Math.sqrt(i + 0.5); // sunflower start
      Object.assign(n, { x: r * Math.cos(angle), y: r * Math.sin(angle), vx: 0, vy: 0, degree: neighbours[i].size });
      n.radius = n.kind === "tag" ? 3.5 : 4.5 + 1.6 * Math.sqrt(n.degree) + (n.kind === "current" ? 1.5 : 0);
      n.el = el("a", { href: new URL(n.url.slice(1), root).href, class: `node node-${n.kind}` });
      const title = el("title"), label = el("text", { dy: n.radius + 13 });
      title.textContent = n.title;
      label.textContent = n.title.length > 30 ? n.title.slice(0, 28) + "…" : n.title;
      n.el.append(title, el("circle", { r: n.radius }), label);
      nodeLayer.append(n.el);
    });
    const edges = links.map(([a, b]) => {
      const line = el("line");
      edgeLayer.append(line);
      return { a: nodes[a], b: nodes[b], ia: a, ib: b, line,
               strength: 1 / Math.min(nodes[a].degree, nodes[b].degree),
               bias: nodes[a].degree / (nodes[a].degree + nodes[b].degree) };
    });

    // Hovering or focusing a node lights it and its neighbours, and dims everything else.
    function highlight(i) {
      svg.classList.toggle("focus", i !== null);
      nodes.forEach((n, j) => n.el.classList.toggle("lit", i !== null && (i === j || neighbours[i].has(j))));
      edges.forEach(e => e.line.classList.toggle("lit", i !== null && (e.ia === i || e.ib === i)));
    }

    // Force layout: nodes repel, links pull like springs, a weak gravity keeps everything in view.
    let alpha = 1, frame = null, dragged = null;
    function tick() {
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j];
          let dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy;
          if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
          const f = (220 * alpha) / d2;
          a.vx -= dx * f; a.vy -= dy * f; b.vx += dx * f; b.vy += dy * f;
        }
      }
      for (const e of edges) {
        let dx = e.b.x + e.b.vx - e.a.x - e.a.vx, dy = e.b.y + e.b.vy - e.a.y - e.a.vy;
        const d = Math.hypot(dx, dy) || 1, f = ((d - 70) / d) * alpha * e.strength;
        dx *= f; dy *= f;
        e.b.vx -= dx * e.bias; e.b.vy -= dy * e.bias;
        e.a.vx += dx * (1 - e.bias); e.a.vy += dy * (1 - e.bias);
      }
      const maxX = width / 2 - 50, maxY = height / 2 - 26;
      for (const n of nodes) {
        if (n === dragged) { n.vx = n.vy = 0; continue; }
        n.vx -= n.x * 0.03 * alpha; n.vy -= n.y * 0.06 * alpha;
        n.x = clamp(n.x + (n.vx *= 0.6), -maxX, maxX);
        n.y = clamp(n.y + (n.vy *= 0.6), -maxY, maxY);
      }
      alpha *= 0.98;
    }
    function render() {
      for (const e of edges) {
        e.line.setAttribute("x1", e.a.x); e.line.setAttribute("y1", e.a.y);
        e.line.setAttribute("x2", e.b.x); e.line.setAttribute("y2", e.b.y);
      }
      for (const n of nodes) n.el.setAttribute("transform", `translate(${n.x.toFixed(1)} ${n.y.toFixed(1)})`);
    }
    function run() {
      if (reduceMotion) {
        while (alpha > 0.005) tick();
        render();
        return;
      }
      if (frame) return;
      const loop = () => {
        tick();
        render();
        frame = alpha > 0.005 || dragged ? requestAnimationFrame(loop) : null;
      };
      frame = requestAnimationFrame(loop);
    }
    function resize() {
      width = figure.clientWidth;
      svg.setAttribute("viewBox", `${-width / 2} ${-height / 2} ${width} ${height}`);
    }

    // Dragging moves a node (and wakes the layout); a drag never counts as a click.
    let start = null, moved = false;
    nodes.forEach((n, i) => {
      n.el.addEventListener("pointerenter", () => highlight(i));
      n.el.addEventListener("pointerleave", () => dragged || highlight(null));
      n.el.addEventListener("focus", () => highlight(i));
      n.el.addEventListener("blur", () => highlight(null));
      n.el.addEventListener("pointerdown", ev => {
        if (ev.button !== 0) return;
        dragged = n; moved = false; start = [ev.clientX, ev.clientY];
        n.el.setPointerCapture(ev.pointerId);
        alpha = Math.max(alpha, 0.3);
        run();
      });
      n.el.addEventListener("pointermove", ev => {
        if (dragged !== n) return;
        if (Math.hypot(ev.clientX - start[0], ev.clientY - start[1]) > 4) moved = true;
        const p = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(svg.getScreenCTM().inverse());
        n.x = clamp(p.x, -width / 2 + 10, width / 2 - 10);
        n.y = clamp(p.y, -height / 2 + 10, height / 2 - 10);
      });
      const release = () => { if (dragged === n) { dragged = null; highlight(null); } };
      n.el.addEventListener("pointerup", release);
      n.el.addEventListener("pointercancel", release);
      n.el.addEventListener("click", ev => { if (moved) { ev.preventDefault(); moved = false; } });
    });

    resize();
    run();
    addEventListener("resize", () => { resize(); alpha = Math.max(alpha, 0.2); run(); });
  }
})();
