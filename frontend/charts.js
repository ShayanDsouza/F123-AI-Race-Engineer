/* Canvas chart engine for distance-aligned telemetry overlays.
   All charts share one x-axis (fraction of lap 0..1, labeled in metres of the
   reference lap) and one synced crosshair. */
"use strict";

const CHART_INK = {
  primary: "#ffffff",
  secondary: "#c3c2b7",
  muted: "#898781",
  grid: "#2c2c2a",
  baseline: "#383835",
  surface: "#1a1a19",
  gain: "#3987e5",   // diverging cool pole: you are faster
  loss: "#e66767",   // diverging warm pole: you are slower
};

const charts = [];
let cursorFrac = null;
let highlightRange = null; // [fromFrac, toFrac] from corner-table hover

function setHighlight(range) {
  highlightRange = range;
  charts.forEach((c) => c.draw());
}

class TelemetryChart {
  /**
   * opts: { el, height, yLabel, series: [{name,color,values,step?}],
   *         grid: number[] (x fractions), refLen: metres,
   *         signFill?: bool, corners?: [{frac,label}], sectors?: [{from,to}],
   *         yFmt?: fn, yMin?, yMax?, unit? }
   */
  constructor(opts) {
    this.o = opts;
    this.wrap = document.createElement("div");
    this.wrap.className = "chart-wrap";
    this.canvas = document.createElement("canvas");
    this.tooltip = document.createElement("div");
    this.tooltip.className = "chart-tooltip";
    this.wrap.appendChild(this.canvas);
    this.wrap.appendChild(this.tooltip);
    opts.el.appendChild(this.wrap);

    this.pad = { l: 46, r: 74, t: 20, b: opts.xAxis ? 24 : 8 };
    const vals = opts.series.flatMap((s) => s.values);
    this.yMin = opts.yMin !== undefined ? opts.yMin : Math.min(...vals);
    this.yMax = opts.yMax !== undefined ? opts.yMax : Math.max(...vals);
    if (this.yMin === this.yMax) this.yMax = this.yMin + 1;
    const span = this.yMax - this.yMin;
    if (opts.yMin === undefined) this.yMin -= span * 0.06;
    if (opts.yMax === undefined) this.yMax += span * 0.06;

    this.canvas.addEventListener("mousemove", (e) => this.onMove(e));
    this.canvas.addEventListener("mouseleave", () => {
      cursorFrac = null;
      this.tooltip.style.display = "none";
      charts.forEach((c) => c.draw());
    });
    new ResizeObserver(() => this.resize()).observe(this.wrap);
    charts.push(this);
    this.resize();
  }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.wrap.clientWidth || 600;
    const h = this.o.height;
    this.w = w; this.h = h;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.canvas.style.width = w + "px";
    this.canvas.style.height = h + "px";
    this.ctx = this.canvas.getContext("2d");
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }

  xPx(frac) { return this.pad.l + frac * (this.w - this.pad.l - this.pad.r); }
  yPx(v) {
    const { t, b } = this.pad;
    return t + (1 - (v - this.yMin) / (this.yMax - this.yMin)) * (this.h - t - b);
  }
  fracAt(px) {
    return Math.min(1, Math.max(0, (px - this.pad.l) / (this.w - this.pad.l - this.pad.r)));
  }

  onMove(e) {
    const rect = this.canvas.getBoundingClientRect();
    cursorFrac = this.fracAt(e.clientX - rect.left);
    charts.forEach((c) => c.draw());
    this.showTooltip(e.clientX - rect.left, e.clientY - rect.top);
  }

  valueAt(series, frac) {
    const g = this.o.grid;
    let i = Math.round(frac * (g.length - 1));
    i = Math.min(g.length - 1, Math.max(0, i));
    return series.values[i];
  }

  showTooltip(mx, my) {
    const o = this.o;
    const dist = Math.round(cursorFrac * o.refLen);
    let html = `<div class="tt-dist">${dist} m</div>`;
    for (const s of o.series) {
      const v = this.valueAt(s, cursorFrac);
      html += `<div class="tt-row"><span class="tt-swatch" style="background:${s.color}"></span>` +
              `<span class="tt-name">${s.name}</span>` +
              `<span class="tt-val">${o.yFmt ? o.yFmt(v) : v.toFixed(1)}${o.unit ? " " + o.unit : ""}</span></div>`;
    }
    this.tooltip.innerHTML = html;
    this.tooltip.style.display = "block";
    const tw = this.tooltip.offsetWidth;
    const left = mx + 14 + tw > this.w ? mx - tw - 12 : mx + 14;
    this.tooltip.style.left = left + "px";
    this.tooltip.style.top = Math.max(4, my - 10) + "px";
  }

  draw() {
    const { ctx, o, pad } = this;
    if (!ctx) return;
    ctx.clearRect(0, 0, this.w, this.h);
    const plotW = this.w - pad.l - pad.r;
    const plotH = this.h - pad.t - pad.b;

    // sector bands (faint alternating)
    if (o.sectors) {
      o.sectors.forEach((s, i) => {
        if (i % 2 === 0) return;
        ctx.fillStyle = "rgba(255,255,255,0.025)";
        ctx.fillRect(this.xPx(s.from), pad.t, (s.to - s.from) * plotW, plotH);
      });
    }
    // corner-table hover highlight
    if (highlightRange) {
      ctx.fillStyle = "rgba(57,135,229,0.10)";
      ctx.fillRect(this.xPx(highlightRange[0]), pad.t,
                   (highlightRange[1] - highlightRange[0]) * plotW, plotH);
    }

    // y grid: 3-4 hairlines
    const ticks = this.yTicks();
    ctx.strokeStyle = CHART_INK.grid;
    ctx.lineWidth = 1;
    ctx.fillStyle = CHART_INK.muted;
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (const tv of ticks) {
      const y = this.yPx(tv);
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(this.w - pad.r, y); ctx.stroke();
      ctx.fillText(o.yFmt ? o.yFmt(tv) : String(Math.round(tv)), pad.l - 6, y);
    }
    // zero baseline for sign charts
    if (o.signFill && this.yMin < 0 && this.yMax > 0) {
      ctx.strokeStyle = CHART_INK.baseline;
      const y0 = this.yPx(0);
      ctx.beginPath(); ctx.moveTo(pad.l, y0); ctx.lineTo(this.w - pad.r, y0); ctx.stroke();
    }

    // x axis labels (metres) on charts that opt in
    if (o.xAxis) {
      ctx.textAlign = "center"; ctx.textBaseline = "top";
      const step = o.refLen > 4500 ? 1000 : 500;
      for (let m = 0; m <= o.refLen; m += step) {
        ctx.fillText((m >= 1000 ? (m / 1000) + "k" : m), this.xPx(m / o.refLen), this.h - pad.b + 6);
      }
    }

    // corner markers
    if (o.corners) {
      ctx.textAlign = "center"; ctx.textBaseline = "top";
      for (const c of o.corners) {
        const x = this.xPx(c.frac);
        ctx.strokeStyle = CHART_INK.grid;
        ctx.setLineDash([2, 4]);
        ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, pad.t + plotH); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = CHART_INK.muted;
        ctx.fillText(c.label, x, 4);
      }
    }

    // series
    const g = o.grid;
    for (const s of o.series) {
      if (o.signFill) this.drawSignFill(s);
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.beginPath();
      for (let i = 0; i < g.length; i++) {
        const x = this.xPx(g[i]);
        const y = this.yPx(s.values[i]);
        if (i === 0) ctx.moveTo(x, y);
        else if (s.step) { ctx.lineTo(x, this.yPx(s.values[i - 1])); ctx.lineTo(x, y); }
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
      // direct label at line end (identity never color-alone)
      ctx.fillStyle = CHART_INK.secondary;
      ctx.textAlign = "left"; ctx.textBaseline = "middle";
      ctx.font = "11px system-ui, sans-serif";
      const lastY = this.yPx(s.values[g.length - 1]);
      ctx.fillStyle = s.color;
      ctx.fillRect(this.w - pad.r + 6, (s.labelY ?? lastY) - 4, 8, 8);
      ctx.fillStyle = CHART_INK.secondary;
      ctx.fillText(s.name, this.w - pad.r + 18, s.labelY ?? lastY);
    }
    this.spreadEndLabels();

    // y-axis title
    ctx.save();
    ctx.fillStyle = CHART_INK.muted;
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "left"; ctx.textBaseline = "top";
    ctx.fillText(o.yLabel, 4, 4);
    ctx.restore();

    // crosshair
    if (cursorFrac !== null) {
      const x = this.xPx(cursorFrac);
      ctx.strokeStyle = "rgba(255,255,255,0.35)";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, pad.t + plotH); ctx.stroke();
      for (const s of o.series) {
        const v = this.valueAt(s, cursorFrac);
        ctx.fillStyle = s.color;
        ctx.beginPath();
        ctx.arc(x, this.yPx(v), 3.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = CHART_INK.surface;
        ctx.lineWidth = 2;
        ctx.stroke();
      }
    }
  }

  drawSignFill(s) {
    const { ctx, o } = this;
    const g = o.grid;
    const y0 = this.yPx(0);
    for (const sign of [1, -1]) {
      ctx.fillStyle = sign > 0 ? "rgba(230,103,103,0.28)" : "rgba(57,135,229,0.28)";
      ctx.beginPath();
      ctx.moveTo(this.xPx(g[0]), y0);
      for (let i = 0; i < g.length; i++) {
        const v = sign > 0 ? Math.max(0, s.values[i]) : Math.min(0, s.values[i]);
        ctx.lineTo(this.xPx(g[i]), this.yPx(v));
      }
      ctx.lineTo(this.xPx(g[g.length - 1]), y0);
      ctx.closePath();
      ctx.fill();
    }
  }

  spreadEndLabels() {
    // stash: labels drawn inline in draw(); collision handling via labelY set once
    if (this._spread || this.o.series.length < 2) return;
    const g = this.o.grid;
    const ys = this.o.series.map((s) => this.yPx(s.values[g.length - 1]));
    if (Math.abs(ys[0] - ys[1]) < 14) {
      const mid = (ys[0] + ys[1]) / 2;
      this.o.series[0].labelY = mid - 8;
      this.o.series[1].labelY = mid + 8;
      this._spread = true;
      this.draw();
    } else this._spread = true;
  }

  yTicks() {
    const span = this.yMax - this.yMin;
    const raw = span / 3;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= 4) || mag * 10;
    const out = [];
    for (let v = Math.ceil(this.yMin / step) * step; v <= this.yMax; v += step) out.push(v);
    return out;
  }
}

function destroyCharts() {
  charts.forEach((c) => c.wrap.remove());
  charts.length = 0;
  cursorFrac = null;
  highlightRange = null;
}
