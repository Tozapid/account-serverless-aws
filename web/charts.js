// Charts of the page: stacked columns by day with an axis, a legend and a
// tooltip on hover. Plain SVG, no library. The colours are the CSS variables
// --series-1 … --series-5 and --series-other, the sixth slot (styles.css),
// assigned in the order of the series, never cycled: a sixth and later series
// is folded into "other" by the caller.
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const SLOTS = 5;

  function svg(tag, attrs, children) {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value != null) node.setAttribute(key, value);
    });
    [].concat(children || []).forEach((child) => child && node.append(child));
    return node;
  }

  function html(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  // Round steps for the axis: 1, 2, 2.5 or 5 times a power of ten.
  function niceMax(value, ticks) {
    if (value <= 0) return 1;
    const raw = value / ticks;
    const power = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((factor) => factor * power).find((candidate) => candidate >= raw);
    return step * ticks;
  }

  function color(index, other) {
    return other ? "var(--series-other)" : `var(--series-${Math.min(index, SLOTS - 1) + 1})`;
  }

  // Series hidden by a click on the legend, per chart (options.key): they
  // stay hidden while the page is open, across redraws.
  const hiddenByChart = {};

  // options: labels (one per column, e.g. days), series ([{key, label,
  // other}]), value(column, key), format(number) for the axis and the
  // tooltip, tick(label) for the labels under the axis, title(label) for
  // the tooltip, extra(label) for one more line of it, height, key (of the
  // chart, to remember the hidden series). Returns an element with the chart
  // and its legend; the chart is drawn again at the width it gets, so that
  // its text and gaps stay in real pixels. A click on a legend entry hides
  // or shows its series, a double click shows it alone; the axis follows
  // what is shown, and every series keeps its colour.
  function columns(options) {
    const { labels } = options;
    // The colour of a series is its place in the whole list, so that hiding
    // one never repaints the others.
    const series = options.series.map((item, position) => ({ ...item, slot: position }));
    if (options.key && !hiddenByChart[options.key]) hiddenByChart[options.key] = new Set();
    const hidden = options.key ? hiddenByChart[options.key] : new Set();
    const wrap = html("div", "chart-wrap");
    const scroller = html("div", "chart-scroll");
    const tip = html("div", "chart-tip hidden");
    tip.setAttribute("role", "status");
    wrap.append(scroller, tip);
    let drawn = 0;
    let available = 640;
    const draw = (width, force) => {
      available = width;
      // Too narrow for its columns, the chart scrolls sideways.
      const size = Math.max(Math.floor(width), labels.length * 10 + 60);
      if (size === drawn && !force) return;
      drawn = size;
      const shown = series.filter((item) => !hidden.has(item.key));
      const totals = labels.map((label) => shown.reduce((sum, item) => sum + Math.max(0, options.value(label, item.key)), 0));
      const max = niceMax(Math.max(0, ...totals), 4);
      scroller.replaceChildren(plot({ ...options, series: shown }, totals, max, size, tip, wrap));
    };
    draw(available);
    if (window.ResizeObserver) {
      new ResizeObserver((entries) => draw(entries[0].contentRect.width)).observe(wrap);
    }
    if (series.length > 1) {
      const legend = html("div", "legend");
      const entries = series.map((item) => {
        const entry = html("button", "legend-item");
        entry.type = "button";
        const swatch = html("span", "swatch");
        swatch.style.background = color(item.slot, item.other);
        entry.append(swatch, document.createTextNode(item.label));
        let timer = null;
        // A single click waits a moment, so that a double click is not two.
        entry.addEventListener("click", () => {
          clearTimeout(timer);
          timer = setTimeout(() => {
            if (hidden.has(item.key)) hidden.delete(item.key);
            else hidden.add(item.key);
            refresh();
          }, 220);
        });
        entry.addEventListener("dblclick", () => {
          clearTimeout(timer);
          const alone = series.every((other) => other.key === item.key || hidden.has(other.key)) && !hidden.has(item.key);
          hidden.clear();
          if (!alone) series.forEach((other) => other.key !== item.key && hidden.add(other.key));
          refresh();
        });
        legend.append(entry);
        return [item, entry];
      });
      const refresh = () => {
        entries.forEach(([item, entry]) => {
          entry.classList.toggle("off", hidden.has(item.key));
          entry.setAttribute("aria-pressed", String(!hidden.has(item.key)));
          if (options.legendTitle) entry.title = options.legendTitle;
        });
        draw(available, true);
      };
      wrap.append(legend);
      refresh();
    }
    return wrap;
  }

  function plot(options, totals, max, width, tip, wrap) {
    const { labels, series, value, format } = options;
    const tick = options.tick || ((label) => label);
    const title = options.title || ((label) => label);
    const height = options.height || 200;
    const pad = { top: 8, right: 8, bottom: 22, left: 64 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const y = (amount) => pad.top + plotH - (amount / max) * plotH;
    const slot = plotW / Math.max(1, labels.length);
    const barW = Math.max(3, Math.min(28, slot - 4));

    const root = svg("svg", { class: "chart", width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": options.label || "" });
    for (let i = 0; i <= 4; i += 1) {
      const amount = (max / 4) * i;
      root.append(svg("line", { class: "grid", x1: pad.left, x2: width - pad.right, y1: y(amount), y2: y(amount) }));
      const text = svg("text", { class: "axis", x: pad.left - 8, y: y(amount) + 4, "text-anchor": "end" });
      text.textContent = format(amount);
      root.append(text);
    }
    const every = Math.max(1, Math.ceil(labels.length / Math.max(1, Math.floor(plotW / 48))));

    labels.forEach((label, index) => {
      const x = pad.left + slot * index + (slot - barW) / 2;
      let top = y(0);
      series.forEach((item) => {
        const amount = Math.max(0, value(label, item.key));
        if (!amount) return;
        const h = (amount / max) * plotH;
        // A 2px gap of the surface between stacked segments.
        const gap = top < y(0) ? 2 : 0;
        if (h - gap >= 0.5) {
          root.append(svg("rect", { class: "mark", x, y: top - h, width: barW, height: h - gap, rx: Math.min(3, (h - gap) / 2), fill: color(item.slot, item.other) }));
        }
        top -= h;
      });
      if (index % every === 0) {
        const text = svg("text", { class: "axis", x: x + barW / 2, y: height - 6, "text-anchor": "middle" });
        text.textContent = tick(label);
        root.append(text);
      }
      // The hover target is the whole column, wider than its bar.
      const hit = svg("rect", { class: "hit", x: pad.left + slot * index, y: pad.top, width: slot, height: plotH, tabindex: "0" });
      const show = () => {
        tip.replaceChildren(html("strong", "", title(label)));
        series.slice().reverse().forEach((item) => {
          const amount = value(label, item.key);
          if (!amount) return;
          const row = html("div", "tip-row");
          const swatch = html("span", "swatch");
          swatch.style.background = color(item.slot, item.other);
          row.append(swatch, html("span", "", item.label), html("span", "tip-value", format(amount)));
          tip.append(row);
        });
        if (options.extra) {
          const more = options.extra(label);
          if (more) tip.append(html("div", "tip-extra", more));
        }
        if (series.length > 1) {
          const row = html("div", "tip-row tip-total");
          row.append(html("span", "", options.totalLabel || ""), html("span", "tip-value", format(totals[index])));
          tip.append(row);
        }
        tip.classList.remove("hidden");
        const box = wrap.getBoundingClientRect();
        const bar = hit.getBoundingClientRect();
        const left = Math.min(Math.max(0, bar.left - box.left + bar.width / 2 - tip.offsetWidth / 2), box.width - tip.offsetWidth);
        tip.style.left = `${left}px`;
        hit.classList.add("active");
      };
      const hide = () => {
        tip.classList.add("hidden");
        hit.classList.remove("active");
      };
      hit.addEventListener("mouseenter", show);
      hit.addEventListener("focus", show);
      hit.addEventListener("mouseleave", hide);
      hit.addEventListener("blur", hide);
      root.append(hit);
    });
    return root;
  }

  // The biggest keys of a map of numbers, at most SLOTS; the rest go into
  // "other", the slot after them.
  function topKeys(totals) {
    const keys = Object.entries(totals).filter(([, amount]) => amount > 0).sort((a, b) => b[1] - a[1]).map(([key]) => key);
    return { kept: keys.slice(0, SLOTS), folded: keys.slice(SLOTS) };
  }

  window.AWS_CHARTS = { columns, topKeys, SLOTS };
})();
