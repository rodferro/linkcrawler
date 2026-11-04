// Link-graph view rendered with Cytoscape.js.
(function () {
  const root = document.getElementById("crawl");
  const container = document.getElementById("graph");
  const info = document.getElementById("node-info");
  const toggle = document.getElementById("uncrawled");
  const layoutSelect = document.getElementById("layout");
  if (!root || !container || typeof cytoscape === "undefined") return;

  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();
  let cy;

  async function load() {
    const res = await fetch(`/api/crawls/${root.dataset.id}/graph?uncrawled=${toggle.checked}`);
    const graph = await res.json();
    const elements = [
      ...graph.nodes.map((n) => ({ data: { ...n } })),
      ...graph.edges.map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target } })),
    ];
    if (cy) cy.destroy();
    cy = cytoscape({ container, elements, minZoom: 0.1, maxZoom: 4 });

    // Size nodes by inbound links relative to this crawl's busiest page.
    const maxIn = Math.max(1, ...cy.nodes().map((n) => n.indegree(false)));
    cy.nodes().forEach((n) => n.data("size", 10 + 26 * Math.sqrt(n.indegree(false) / maxIn)));
    // Label only the start page and the main hubs; everything else on hover/select.
    cy.nodes()
      .sort((a, b) => b.indegree(false) - a.indegree(false))
      .slice(0, 6)
      .union(cy.nodes('[kind = "start"]'))
      .addClass("labeled");

    cy.style([
      {
        selector: "node",
        style: {
          "background-color": color("--node-ok"),
          width: "data(size)",
          height: "data(size)",
          label: "",
          "font-size": 10,
          color: color("--text"),
          "text-valign": "bottom",
          "text-margin-y": 4,
          "text-max-width": 160,
          "text-wrap": "ellipsis",
          "text-background-color": color("--surface"),
          "text-background-opacity": 0.85,
          "text-background-padding": 2,
        },
      },
      { selector: "node.labeled, node.hover, node:selected", style: { label: "data(label)", "z-index": 10 } },
      { selector: 'node[kind = "start"]', style: { "background-color": color("--node-start") } },
      { selector: 'node[kind = "error"]', style: { "background-color": color("--node-error") } },
      { selector: 'node[kind = "uncrawled"]', style: { "background-color": color("--node-uncrawled") } },
      {
        selector: "edge",
        style: {
          width: 1,
          "line-color": color("--edge"),
          "curve-style": "haystack",
          opacity: 0.45,
        },
      },
      {
        selector: "edge.focus",
        style: {
          "line-color": color("--accent"),
          opacity: 0.9,
          "curve-style": "bezier",
          "target-arrow-shape": "triangle",
          "target-arrow-color": color("--accent"),
          "arrow-scale": 0.7,
        },
      },
      { selector: "node:selected", style: { "border-width": 3, "border-color": color("--accent") } },
      { selector: ".faded", style: { opacity: 0.08 } },
    ]);
    runLayout();
    // Keep labels a constant on-screen size whatever the zoom level.
    const scaleLabels = () =>
      cy.style().selector("node").style({ "font-size": 11 / cy.zoom(), "text-max-width": 160 / cy.zoom() }).update();
    cy.on("zoom", scaleLabels);
    scaleLabels();

    cy.on("mouseover", "node", (e) => e.target.addClass("hover"));
    cy.on("mouseout", "node", (e) => e.target.removeClass("hover"));
    cy.on("tap", "node", (evt) => {
      const n = evt.target;
      const d = n.data();
      cy.elements().addClass("faded").removeClass("focus");
      n.closedNeighborhood().removeClass("faded");
      n.connectedEdges().addClass("focus");
      info.classList.remove("muted");
      info.innerHTML = "";
      const link = document.createElement("a");
      link.href = d.id;
      link.target = "_blank";
      link.rel = "noreferrer noopener";
      link.textContent = d.id;
      const meta = document.createElement("div");
      meta.className = "muted";
      meta.textContent = [
        d.title,
        d.status_code ? `HTTP ${d.status_code}` : d.kind,
        d.depth != null ? `depth ${d.depth}` : null,
        `${n.indegree(false)} in · ${n.outdegree(false)} out`,
      ].filter(Boolean).join(" · ");
      info.append(link, meta);
    });
    cy.on("tap", (evt) => {
      if (evt.target === cy) cy.elements().removeClass("faded focus");
    });

    if (graph.truncated) {
      info.textContent = "Graph truncated: too many uncrawled targets to display.";
    }
  }

  // One ring per crawl depth (uncrawled targets outermost). Each ring is at least
  // `gap` further out than the previous one and big enough to fit its nodes.
  function ringPositions(maxDepth) {
    const rings = new Map();
    cy.nodes().forEach((n) => {
      const level = n.data("depth") ?? maxDepth + 1;
      if (!rings.has(level)) rings.set(level, []);
      rings.get(level).push(n);
    });
    const positions = {};
    const gap = 90;
    let radius = 0;
    [...rings.keys()].sort((a, b) => a - b).forEach((level, i) => {
      const nodes = rings.get(level).sort((a, b) => a.id().localeCompare(b.id()));
      const fit = (nodes.length * 26) / (2 * Math.PI);
      radius = i === 0 && nodes.length === 1 ? 0 : Math.max(radius + gap, fit);
      nodes.forEach((n, j) => {
        const angle = (2 * Math.PI * j) / nodes.length - Math.PI / 2;
        positions[n.id()] = { x: radius * Math.cos(angle), y: radius * Math.sin(angle) };
      });
    });
    return positions;
  }

  function runLayout() {
    const maxDepth = Math.max(0, ...cy.nodes().map((n) => n.data("depth") ?? 0));
    const layouts = {
      // Breadth-first crawl => rings by depth: start page in the middle.
      concentric: { name: "preset", positions: ringPositions(maxDepth) },
      cose: { name: "cose", nodeRepulsion: () => 20000, idealEdgeLength: () => 80, gravity: 0.6 },
      breadthfirst: { name: "breadthfirst", directed: true, roots: cy.nodes('[kind = "start"]'), spacingFactor: 1.1 },
    };
    cy.layout({ ...layouts[layoutSelect.value], animate: false, fit: true, padding: 30 }).run();
  }

  toggle.addEventListener("change", load);
  layoutSelect.addEventListener("change", () => cy && runLayout());
  document.getElementById("fit").addEventListener("click", () => cy && cy.fit(undefined, 30));
  load();
})();
