// Live progress for a crawl: polls the JSON API while the crawl is running.
(function () {
  const root = document.getElementById("crawl");
  if (!root) return;
  const id = root.dataset.id;
  const api = `/api/crawls/${id}`;
  const pagesBody = document.querySelector("#pages tbody");
  let lastCount = -1;

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  function renderPages(pages) {
    pagesBody.innerHTML = pages
      .map(
        (p) => `<tr>
        <td class="num">${p.depth}</td>
        <td class="url"><a href="${esc(p.url)}" rel="noreferrer noopener" target="_blank">${esc(p.url)}</a></td>
        <td>${esc(p.title)}${p.error ? ` <span class="error">${esc(p.error)}</span>` : ""}</td>
        <td class="num"><span class="code code-${Math.floor((p.status_code || 0) / 100)}">${p.status_code ?? "–"}</span></td>
        <td class="num">${p.outgoing_links}</td>
        <td class="num muted">${p.elapsed_ms != null ? p.elapsed_ms + " ms" : ""}</td>
      </tr>`
      )
      .join("");
  }

  function update(c) {
    for (const key of ["pages_crawled", "pages_failed", "links_found"]) {
      const el = root.querySelector(`[data-stat="${key}"]`);
      if (el) el.textContent = c[key];
    }
    if (c.started_at) {
      const end = c.finished_at ? new Date(c.finished_at) : new Date();
      const secs = (end - new Date(c.started_at)) / 1000;
      root.querySelector('[data-stat="duration"]').textContent = `${Math.max(0, secs).toFixed(1)}s`;
    }
    const badge = root.querySelector("[data-status]");
    badge.textContent = c.status;
    badge.className = `badge badge-${c.status}`;
    root.querySelector(".progress .bar").style.width = `${Math.min(100, (100 * c.pages_crawled) / c.max_pages)}%`;
  }

  async function poll() {
    try {
      const res = await fetch(api);
      if (!res.ok) throw new Error(res.status);
      const crawl = await res.json();
      update(crawl);
      if (pagesBody && crawl.pages_crawled !== lastCount) {
        lastCount = crawl.pages_crawled;
        renderPages(await (await fetch(`${api}/pages`)).json());
      }
      if (!["queued", "running"].includes(crawl.status)) {
        window.location.reload(); // final render from the server
        return;
      }
    } catch (err) {
      console.warn("poll failed", err);
    }
    setTimeout(poll, 1000);
  }

  const cancel = document.getElementById("cancel");
  if (cancel) {
    cancel.addEventListener("click", async () => {
      cancel.disabled = true;
      await fetch(`${api}/cancel`, { method: "POST" });
    });
  }

  if (root.dataset.finished === "false") poll();
})();
